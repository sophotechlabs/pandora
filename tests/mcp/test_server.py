import pytest
from asgiref.sync import sync_to_async

from pandora.core import models as core_models
from pandora.mcp import server, tools

pytestmark = pytest.mark.django_db


@pytest.fixture
def read_token(project, monkeypatch):
    token = core_models.IngestToken.objects.create(
        project=project,
        name="mcp",
        token="read-token-value",
        scope=core_models.TokenScope.READ,
    )
    monkeypatch.setenv(server.TOKEN_ENV, "read-token-value")
    return token


# what an agent sees


async def test_the_server_advertises_its_read_only_tools():
    """Should offer exactly the four reads — an agent that can write into triage is a different decision."""
    listed = await server.build().list_tools()

    result = sorted(tool.name for tool in listed)
    expected = ["get_issue", "get_issue_events", "issue_as_markdown", "search_issues"]

    assert result == expected


async def test_every_tool_describes_itself():
    """Should let a model choose the right one without guessing from the name."""
    listed = await server.build().list_tools()

    result = [tool.name for tool in listed if not tool.description]
    expected = []

    assert result == expected


async def test_the_server_explains_the_query_language():
    """Should carry the search syntax in its instructions, or every search is a guess."""
    instructions = server.build().instructions or ""

    result = ("is:unresolved" in instructions, "label:" in instructions)
    expected = (True, True)

    assert result == expected


def test_the_server_is_named_for_the_install():
    """Should let one agent talk to more than one Pandora without confusing them."""
    result = server.build("pandora-p-mk1").name
    expected = "pandora-p-mk1"

    assert result == expected


# the token


def test_a_missing_token_variable_is_an_error(monkeypatch):
    """Should refuse to start unauthenticated rather than reading whatever it can reach."""
    monkeypatch.delenv(server.TOKEN_ENV, raising=False)

    with pytest.raises(tools.ToolError, match=server.TOKEN_ENV):
        server.token()


def test_a_blank_token_variable_is_an_error(monkeypatch):
    """Should treat an empty variable the same as an absent one."""
    monkeypatch.setenv(server.TOKEN_ENV, "   ")

    with pytest.raises(tools.ToolError, match=server.TOKEN_ENV):
        server.token()


def test_the_token_resolves_to_its_project(read_token):
    """Should scope every call to the project the credential belongs to."""
    result = server.token().project_id
    expected = read_token.project_id

    assert result == expected


# the tools reach the logic


@pytest.mark.django_db(transaction=True)
async def test_search_returns_issues_through_the_tool_call(read_token, make_issue):
    """Should exercise the whole path an agent takes, not only the function behind it."""
    await sync_to_async(make_issue)(title="Something is on fire")

    found = await server.build().call_tool("search_issues", {"query": "is:unresolved"})

    result = [row["title"] for row in found.structured_content["results"]]
    expected = ["Something is on fire"]

    assert result == expected


@pytest.mark.django_db(transaction=True)
async def test_markdown_comes_back_through_the_tool_call(read_token, make_issue):
    """Should hand back the rendered document rather than a structure to reassemble."""
    issue = await sync_to_async(make_issue)(title="PaymentGatewayError")

    found = await server.build().call_tool("issue_as_markdown", {"issue_id": issue.pk})

    result = found.content[0].text.splitlines()[0]
    expected = "# PaymentGatewayError"

    assert result == expected


@pytest.mark.django_db(transaction=True)
async def test_one_issue_comes_back_through_the_tool_call(read_token, make_issue):
    """Should reach the detail path an agent uses after a search narrows things down."""
    issue = await sync_to_async(make_issue)(title="TargetDown")

    found = await server.build().call_tool("get_issue", {"issue_id": issue.pk})

    result = found.structured_content["title"]
    expected = "TargetDown"

    assert result == expected


@pytest.mark.django_db(transaction=True)
async def test_occurrences_come_back_through_the_tool_call(read_token, make_issue):
    """Should reach the stack traces, which is the deepest an agent needs to go."""
    issue = await sync_to_async(make_issue)(title="boom")

    found = await server.build().call_tool(
        "get_issue_events", {"issue_id": issue.pk, "limit": 1}
    )

    result = found.structured_content["supported"]

    assert result is True
