"""Checks for selecting a development API port without disturbing other servers."""

import errno
from unittest.mock import MagicMock, call, patch

import pytest

from scripts.dev_servers import available_api_port


def test_uses_default_api_port_when_available():
    with patch("scripts.dev_servers.socket.socket") as socket_factory:
        probe = socket_factory.return_value.__enter__.return_value
        probe.getsockname.return_value = ("127.0.0.1", 8765)
        assert available_api_port() == 8765
        probe.bind.assert_called_once_with(("127.0.0.1", 8765))


def test_uses_free_port_when_default_is_occupied():
    with patch("scripts.dev_servers.socket.socket") as socket_factory:
        probe = socket_factory.return_value.__enter__.return_value
        probe.bind.side_effect = [OSError(errno.EADDRINUSE, "occupied"), None]
        probe.getsockname.return_value = ("127.0.0.1", 43210)
        assert available_api_port() == 43210
        assert probe.bind.call_args_list == [
            call(("127.0.0.1", 8765)),
            call(("127.0.0.1", 0)),
        ]


def test_other_bind_errors_are_not_treated_as_port_conflicts():
    with patch("scripts.dev_servers.socket.socket") as socket_factory:
        probe: MagicMock = socket_factory.return_value.__enter__.return_value
        probe.bind.side_effect = OSError(errno.EACCES, "denied")
        with pytest.raises(OSError) as error:
            available_api_port()
        assert error.value.errno == errno.EACCES
