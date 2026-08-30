from __future__ import annotations

import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from django.contrib.auth import get_user_model

from pandora.core.models import DsnKey, IngestToken, Project, ServiceLink
from pandora.issues.models import GroupingRule, PathRule
from pandora.people import ownership
from pandora.people.models import Membership, OwnershipRule, Role, Team

SECTIONS = (
    "projects",
    "tokens",
    "dsn_keys",
    "grouping_rules",
    "path_rules",
    "service_links",
    "teams",
    "ownership_rules",
)


class ConfigError(ValueError):
    pass


@dataclass
class Report:
    created: list[str] = field(default_factory=list)
    updated: list[str] = field(default_factory=list)
    deactivated: list[str] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)

    def lines(self) -> list[str]:
        rows: list[str] = []
        for label, names in (
            ("create", self.created),
            ("update", self.updated),
            ("deactivate", self.deactivated),
        ):
            rows.extend(f"{label} {name}" for name in names)
        return rows


def load(path: str | Path) -> dict[str, Any]:
    text = Path(path).read_text(encoding="utf-8")
    parsed = yaml.safe_load(text)
    if parsed is None:
        return {}
    if not isinstance(parsed, Mapping):
        raise ConfigError("the config file must be a mapping of sections")
    unknown = sorted(set(parsed) - set(SECTIONS))
    if unknown:
        raise ConfigError(f"unknown section(s): {', '.join(unknown)}")
    return dict(parsed)


def _rows(document: Mapping[str, Any], section: str) -> list[Mapping[str, Any]]:
    rows = document.get(section) or []
    if not isinstance(rows, Sequence) or isinstance(rows, str):
        raise ConfigError(f"{section} must be a list")
    for row in rows:
        if not isinstance(row, Mapping):
            raise ConfigError(f"every {section} entry must be a mapping")
    return list(rows)


def _required(row: Mapping[str, Any], section: str, *names: str) -> None:
    missing = [name for name in names if not row.get(name)]
    if missing:
        raise ConfigError(f"{section} entry is missing {', '.join(missing)}")


def _secret(row: Mapping[str, Any], section: str, key: str) -> str:
    literal = row.get(key)
    variable = row.get(f"{key}_env")
    if literal and variable:
        raise ConfigError(f"{section} entry sets both {key} and {key}_env")
    if literal:
        return str(literal)
    if not variable:
        raise ConfigError(f"{section} entry needs {key} or {key}_env")
    value = os.environ.get(str(variable), "")
    if not value:
        raise ConfigError(f"{variable} is empty or unset")
    return value


def apply(document: Mapping[str, Any]) -> Report:
    report = Report()
    _apply_projects(document, report)
    projects = {project.slug: project for project in Project.objects.all()}
    _apply_tokens(document, projects, report)
    _apply_dsn_keys(document, projects, report)
    _apply_grouping_rules(document, projects, report)
    _apply_path_rules(document, projects, report)
    _apply_service_links(document, projects, report)
    _apply_teams(document, projects, report)
    _apply_ownership_rules(document, projects, report)
    return report


def _project(projects: Mapping[str, Project], slug: Any, section: str) -> Project:
    project = projects.get(str(slug))
    if project is None:
        raise ConfigError(f"{section} entry names unknown project {slug!r}")
    return project


def _record(report: Report, created: bool, changed: bool, name: str) -> None:
    if created:
        report.created.append(name)
    elif changed:
        report.updated.append(name)
    else:
        report.unchanged.append(name)


def _write(instance: Any, fields: Mapping[str, Any]) -> bool:
    changed = [
        name for name, value in fields.items() if getattr(instance, name) != value
    ]
    if not changed:
        return False
    for name in changed:
        setattr(instance, name, fields[name])
    instance.save(update_fields=changed)
    return True


def _apply_projects(document: Mapping[str, Any], report: Report) -> None:
    for row in _rows(document, "projects"):
        _required(row, "projects", "slug", "name")
        project, created = Project.objects.get_or_create(
            slug=str(row["slug"]), defaults={"name": str(row["name"])}
        )
        changed = _write(project, {"name": str(row["name"])})
        _record(report, created, changed, f"project {project.slug}")


def _apply_tokens(
    document: Mapping[str, Any], projects: Mapping[str, Project], report: Report
) -> None:
    declared = []
    for row in _rows(document, "tokens"):
        _required(row, "tokens", "name", "project")
        project = _project(projects, row["project"], "tokens")
        token, created = IngestToken.objects.get_or_create(
            project=project,
            name=str(row["name"]),
            defaults={"token": _secret(row, "tokens", "token")},
        )
        changed = _write(
            token,
            {
                "token": _secret(row, "tokens", "token"),
                "source": str(row.get("source", "am")),
                "scope": str(row.get("scope", "ingest")),
                "environment": str(row.get("environment", "")),
                "active": True,
            },
        )
        declared.append(token.pk)
        _record(report, created, changed, f"token {project.slug}/{token.name}")
    _deactivate(IngestToken.objects.exclude(pk__in=declared), report, "token")


def _apply_dsn_keys(
    document: Mapping[str, Any], projects: Mapping[str, Project], report: Report
) -> None:
    declared = []
    for row in _rows(document, "dsn_keys"):
        _required(row, "dsn_keys", "project")
        project = _project(projects, row["project"], "dsn_keys")
        public_key = _secret(row, "dsn_keys", "public_key")
        key, created = DsnKey.objects.get_or_create(
            public_key=public_key, defaults={"project": project}
        )
        changed = _write(key, {"project": project, "active": True})
        declared.append(key.pk)
        _record(report, created, changed, f"dsn key {project.slug}/{public_key[:8]}")
    _deactivate(DsnKey.objects.exclude(pk__in=declared), report, "dsn key")


def _apply_grouping_rules(
    document: Mapping[str, Any], projects: Mapping[str, Project], report: Report
) -> None:
    declared = []
    for row in _rows(document, "grouping_rules"):
        _required(row, "grouping_rules", "priority")
        project = None
        if row.get("project"):
            project = _project(projects, row["project"], "grouping_rules")
        rule, created = GroupingRule.objects.get_or_create(
            priority=int(row["priority"]),
            project=project,
            alertname_regex=str(row.get("alertname_regex", "")),
            defaults={
                "mode": str(row.get("mode", "denylist")),
                "labels": list(row.get("labels", [])),
            },
        )
        changed = _write(
            rule,
            {
                "mode": str(row.get("mode", "denylist")),
                "labels": list(row.get("labels", [])),
                "active": True,
            },
        )
        declared.append(rule.pk)
        _record(report, created, changed, f"grouping rule {rule.priority}")
    _deactivate(GroupingRule.objects.exclude(pk__in=declared), report, "grouping rule")


def _apply_path_rules(
    document: Mapping[str, Any], projects: Mapping[str, Project], report: Report
) -> None:
    declared = []
    for row in _rows(document, "path_rules"):
        _required(row, "path_rules", "name", "pattern")
        project = None
        if row.get("project"):
            project = _project(projects, row["project"], "path_rules")
        rule, created = PathRule.objects.get_or_create(
            name=str(row["name"]),
            project=project,
            defaults={"pattern": str(row["pattern"])},
        )
        changed = _write(
            rule,
            {
                "pattern": str(row["pattern"]),
                "replacement": str(row.get("replacement", "")),
                "ordering": int(row.get("ordering", 100)),
                "active": True,
            },
        )
        declared.append(rule.pk)
        _record(report, created, changed, f"path rule {rule.name}")
    _deactivate(PathRule.objects.exclude(pk__in=declared), report, "path rule")


def _apply_service_links(
    document: Mapping[str, Any], projects: Mapping[str, Project], report: Report
) -> None:
    declared = []
    for row in _rows(document, "service_links"):
        _required(row, "service_links", "name", "url_template")
        project = None
        if row.get("project"):
            project = _project(projects, row["project"], "service_links")
        link, created = ServiceLink.objects.get_or_create(
            name=str(row["name"]),
            project=project,
            defaults={"url_template": str(row["url_template"])},
        )
        changed = _write(
            link,
            {
                "url_template": str(row["url_template"]),
                "ordering": int(row.get("ordering", 100)),
                "active": True,
            },
        )
        declared.append(link.pk)
        _record(report, created, changed, f"service link {link.name}")
    _deactivate(ServiceLink.objects.exclude(pk__in=declared), report, "service link")


def _account(username: str) -> Any:
    model = get_user_model()
    user, created = model.objects.get_or_create(
        username=username, defaults={"is_staff": True}
    )
    if created:
        user.set_unusable_password()
        user.save(update_fields=["password"])
    return user


def _members(row: Mapping[str, Any], team: Team) -> None:
    declared = []
    for entry in row.get("members", []) or []:
        username = ""
        role = str(Role.MEMBER)
        if isinstance(entry, str):
            username = entry
        else:
            username = str(entry.get("user", ""))
            role = str(entry.get("role", Role.MEMBER))
        if not username:
            raise ConfigError("teams entry has a member with no user")
        if role not in Role.values:
            raise ConfigError(f"teams entry names unknown role {role!r}")
        user = _account(username)
        membership, _ = Membership.objects.get_or_create(team=team, user=user)
        _write(membership, {"role": role})
        declared.append(user.pk)
    Membership.objects.filter(team=team).exclude(user_id__in=declared).delete()


def _apply_teams(
    document: Mapping[str, Any], projects: Mapping[str, Project], report: Report
) -> None:
    for row in _rows(document, "teams"):
        _required(row, "teams", "name")
        team, created = Team.objects.get_or_create(name=str(row["name"]))
        wanted = [
            _project(projects, slug, "teams") for slug in row.get("projects", []) or []
        ]
        changed = set(team.projects.all()) != set(wanted)
        team.projects.set(wanted)
        before = set(Membership.objects.filter(team=team).values_list("user", "role"))
        _members(row, team)
        after = set(Membership.objects.filter(team=team).values_list("user", "role"))
        _record(report, created, changed or before != after, f"team {team.name}")


def _apply_ownership_rules(
    document: Mapping[str, Any], projects: Mapping[str, Project], report: Report
) -> None:
    declared = []
    for row in _rows(document, "ownership_rules"):
        _required(row, "ownership_rules", "name", "pattern")
        if row.get("team") and row.get("user"):
            raise ConfigError(
                f"ownership rule {row['name']!r} names both a team and a user"
            )
        if not row.get("team") and not row.get("user"):
            raise ConfigError(f"ownership rule {row['name']!r} names no owner")
        field = str(row.get("field", ownership.PATH))
        if field not in ownership.FIELDS:
            raise ConfigError(
                f"ownership rule {row['name']!r} matches on unknown field {field!r}"
            )
        team = None
        if row.get("team"):
            team = Team.objects.filter(name=str(row["team"])).first()
            if team is None:
                raise ConfigError(
                    f"ownership rule {row['name']!r} names unknown team {row['team']!r}"
                )
        user = None
        if row.get("user"):
            user = _account(str(row["user"]))
        project = None
        if row.get("project"):
            project = _project(projects, row["project"], "ownership_rules")
        rule, created = OwnershipRule.objects.get_or_create(
            name=str(row["name"]),
            defaults={"pattern": str(row["pattern"])},
        )
        changed = _write(
            rule,
            {
                "pattern": str(row["pattern"]),
                "field": field,
                "team": team,
                "user": user,
                "project": project,
                "ordering": int(row.get("ordering", 100)),
                "active": True,
            },
        )
        declared.append(rule.pk)
        _record(report, created, changed, f"ownership rule {rule.name}")
    _deactivate(
        OwnershipRule.objects.exclude(pk__in=declared), report, "ownership rule"
    )


def _deactivate(queryset: Any, report: Report, label: str) -> None:
    for row in queryset.filter(active=True):
        row.active = False
        row.save(update_fields=["active"])
        report.deactivated.append(f"{label} {row}")
