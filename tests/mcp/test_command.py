import io

import pytest
from django.core import management
from django.core.management.base import CommandError


def test_the_command_serves_over_stdio(mocker):
    """Should start the transport an agent connects on, without a port to configure."""
    built = mocker.Mock()
    mocker.patch("pandora.mcp.server.build", return_value=built)

    management.call_command("mcp", stdout=io.StringIO())

    built.run.assert_called_once_with(transport="stdio")


def test_the_server_is_named_from_the_flag(mocker):
    """Should let one agent hold two installs apart."""
    build = mocker.patch("pandora.mcp.server.build")

    management.call_command("mcp", "--name", "pandora-p-mk1", stdout=io.StringIO())

    build.assert_called_once_with("pandora-p-mk1")


def test_a_missing_extra_is_reported_as_a_command_error(mocker):
    """Should say how to install it rather than showing an import traceback."""
    mocker.patch.dict("sys.modules", {"pandora.mcp.server": None})

    with pytest.raises(CommandError, match="pip install 'pandora\\[mcp\\]'"):
        management.call_command("mcp", stdout=io.StringIO())
