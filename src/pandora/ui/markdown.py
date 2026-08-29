from __future__ import annotations

from datetime import datetime

from pandora.events.types import Event
from pandora.issues import detail as issue_detail
from pandora.issues.models import Issue
from pandora.ui import event_view

EVENT_LIMIT = 3
FRAME_LIMIT = 12
CRUMB_LIMIT = 10


def render(issue: Issue, detail: issue_detail.Detail, events: list[Event]) -> str:
    blocks = [_header(issue), _facts(issue)]
    blocks.extend(_events(events))
    blocks.append(_episodes(detail))
    blocks.append(_tags(detail))
    blocks.append(_correlated(detail))
    blocks.append(_links(detail))
    blocks.append(_activity(detail))
    return "\n\n".join(block for block in blocks if block) + "\n"


def _stamp(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%d %H:%M:%SZ")


def _header(issue: Issue) -> str:
    lines = [f"# {issue.title}"]
    if issue.culprit:
        lines.append(f"`{issue.culprit}`")
    return "\n\n".join(lines)


def _facts(issue: Issue) -> str:
    rows = [
        ("Project", issue.project.slug),
        ("Environment", issue.environment or "—"),
        ("Level", issue.get_level_display()),
        ("Triage", issue.get_triage_state_display()),
        ("Source", issue.source_state or "—"),
        ("Events", str(issue.event_count)),
        ("Open episodes", str(issue.open_episode_count)),
        ("First seen", _stamp(issue.first_seen)),
        ("Last seen", _stamp(issue.last_seen)),
        ("Fingerprint", " · ".join(issue.fingerprint) or issue.fingerprint_hash),
    ]
    lines = ["| | |", "|---|---|"]
    lines.extend(f"| {name} | {value} |" for name, value in rows)
    return "\n".join(lines)


def _events(events: list[Event]) -> list[str]:
    blocks = []
    for event in events[:EVENT_LIMIT]:
        blocks.append(_event(event))
    return [block for block in blocks if block]


def _event(event: Event) -> str:
    lines = [
        f"## Occurrence {event.id}",
        f"`{_stamp(event.timestamp)}` {event.message}",
    ]
    body = event_view.build(event.payload)
    if body is None:
        return "\n\n".join(lines)
    for block in body.exceptions:
        lines.append(_exception(block))
    crumbs = _breadcrumbs(body)
    if crumbs:
        lines.append(crumbs)
    return "\n\n".join(lines)


def _exception(block: event_view.ExceptionBlock) -> str:
    head = block.kind
    if block.caused_by:
        head = f"Caused by: {block.kind}"
    lines = [f"### {head}"]
    if block.value:
        lines.append(block.value)
    frames = [_frame(frame) for frame in block.frames[:FRAME_LIMIT]]
    if frames:
        lines.append("```\n" + "\n".join(frames) + "\n```")
    if len(block.frames) > FRAME_LIMIT:
        lines.append(f"_{len(block.frames) - FRAME_LIMIT} more frames_")
    return "\n\n".join(lines)


def _frame(frame: event_view.FrameRow) -> str:
    marker = "  "
    if frame.in_app:
        marker = "> "
    where = frame.filename
    if frame.lineno is not None:
        where = f"{frame.filename}:{frame.lineno}"
    line = f"{marker}{frame.location} — {where}"
    current = [row for row in frame.context if row.current]
    if current:
        line = f"{line}\n      {current[0].text.strip()}"
    return line


def _breadcrumbs(body: event_view.EventBody) -> str:
    if not body.breadcrumbs:
        return ""
    lines = ["### Breadcrumbs", "```"]
    for crumb in body.breadcrumbs[:CRUMB_LIMIT]:
        lines.append(f"{crumb.stamp} {crumb.level} {crumb.category} {crumb.message}")
    lines.append("```")
    return "\n".join(lines)


def _episodes(detail: issue_detail.Detail) -> str:
    rows = detail.timeline.rows
    if not rows:
        return ""
    lines = [
        "## Episodes",
        "| Started | Ended | Duration | Deliveries | Labels |",
        "|---|---|---|---|---|",
    ]
    for row in rows:
        cells = " | ".join(cell.text for cell in row)
        lines.append(f"| {cells} |")
    return "\n".join(lines)


def _tags(detail: issue_detail.Detail) -> str:
    if not detail.tags:
        return ""
    lines = ["## Tags"]
    for group in detail.tags:
        values = ", ".join(f"{bar.label} ({bar.count})" for bar in group.bars)
        lines.append(f"- **{group.key}** — {values}")
    return "\n".join(lines)


def _correlated(detail: issue_detail.Detail) -> str:
    matches = detail.correlation.matches
    if not matches:
        return ""
    lines = ["## Firing in the same window"]
    for match in matches:
        shared = ", ".join(f"{key}={value}" for key, value in match.shared)
        lines.append(
            f"- {match.issue.title} — x{match.ratio:.1f} its usual rate, "
            f"{match.count} in window ({shared})"
        )
    return "\n".join(lines)


def _links(detail: issue_detail.Detail) -> str:
    if not detail.links:
        return ""
    lines = ["## Links"]
    lines.extend(f"- [{link.label}]({link.href})" for link in detail.links)
    return "\n".join(lines)


def _activity(detail: issue_detail.Detail) -> str:
    if not detail.activities:
        return ""
    lines = ["## Activity"]
    for row in detail.activities:
        actor = row.actor or "pandora"
        note = ""
        if row.note:
            note = f" ({row.note})"
        lines.append(f"- `{_stamp(row.at)}` {row.kind} by {actor}{note}")
    return "\n".join(lines)
