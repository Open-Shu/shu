"""Unit tests for SandboxLauncher (parent-side subprocess orchestrator)."""

from __future__ import annotations

import asyncio
import os
import signal
import sys
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest

from _module_loader import load_module as _load_module

_SANDBOX_DIR = Path(__file__).resolve().parents[4] / "shu" / "plugins" / "sandbox"
_HOST_DIR = Path(__file__).resolve().parents[4] / "shu" / "plugins" / "host"
_PLUGINS_DIR = Path(__file__).resolve().parents[4] / "shu" / "plugins"
_CORE_DIR = Path(__file__).resolve().parents[4] / "shu" / "core"


if "shu" not in sys.modules:
    sys.modules["shu"] = MagicMock()
if "shu.plugins" not in sys.modules:
    sys.modules["shu.plugins"] = MagicMock()
if "shu.plugins.host" not in sys.modules:
    sys.modules["shu.plugins.host"] = MagicMock()
if "shu.plugins.sandbox" not in sys.modules:
    sys.modules["shu.plugins.sandbox"] = MagicMock()

_host_base_mod = _load_module("shu.plugins.host.base", _HOST_DIR / "base.py")
_host_exc_mod = _load_module("shu.plugins.host.exceptions", _HOST_DIR / "exceptions.py")
_base_mod = _load_module("shu.plugins.base", _PLUGINS_DIR / "base.py")
_rpc_mod = _load_module("shu.plugins.sandbox.rpc", _SANDBOX_DIR / "rpc.py")
_exc_mod = _load_module("shu.plugins.sandbox.exceptions", _SANDBOX_DIR / "exceptions.py")

# RpcServer is imported by launcher.py — load it before the launcher.
_identity_mod = _load_module(
    "shu.plugins.host.identity_capability", _HOST_DIR / "identity_capability.py"
)
_log_mod = _load_module(
    "shu.plugins.host.log_capability", _HOST_DIR / "log_capability.py"
)
_utils_mod = _load_module(
    "shu.plugins.host.utils_capability", _HOST_DIR / "utils_capability.py"
)
_server_mod = _load_module(
    "shu.plugins.sandbox.rpc_server", _SANDBOX_DIR / "rpc_server.py"
)
_server_mod.logger = MagicMock()

_launcher_mod = _load_module(
    "shu.plugins.sandbox.launcher", _SANDBOX_DIR / "launcher.py"
)
_launcher_mod.logger = MagicMock()

SandboxLauncher = _launcher_mod.SandboxLauncher
_ChildCrashedBeforeConnect = _launcher_mod._ChildCrashedBeforeConnect
PluginResult = _base_mod.PluginResult
CAP_NAMES = _host_base_mod.CAP_NAMES


class _FakeHost:
    """Mimics Host's declared-caps gate without importing host_builder."""

    def __init__(self, declared_caps: set[str] | None = None) -> None:
        object.__setattr__(self, "_declared_caps", declared_caps or set())


def _make_launcher(timeout: int = 30) -> SandboxLauncher:
    mock_settings = MagicMock()
    return SandboxLauncher(timeout_seconds=timeout, settings=mock_settings)


def _make_mock_process(
    returncode: int | None = None,
    stderr_data: bytes = b"",
) -> MagicMock:
    """Build a mock asyncio.subprocess.Process with controllable behaviour."""
    process = MagicMock()
    process.returncode = returncode
    process.pid = 12345

    stderr_reader = AsyncMock()
    stderr_reader.read = AsyncMock(return_value=stderr_data)
    process.stderr = stderr_reader

    process.wait = AsyncMock(return_value=returncode or 0)
    process.send_signal = MagicMock()
    process.kill = MagicMock()

    return process


class TestScrubEnv:
    """Tests for SandboxLauncher._scrubbed_env."""

    def test_only_allowed_keys_present(self) -> None:
        """env dict contains exactly PATH, HOME, LANG (if set) and PYTHONPATH."""
        launcher = _make_launcher()

        with patch.dict(
            os.environ,
            {
                "PATH": "/usr/bin",
                "HOME": "/home/test",
                "LANG": "en_US.UTF-8",
                "SECRET_KEY": "should-not-appear",
                "AWS_ACCESS_KEY_ID": "should-not-appear",
                "PYTHONPATH": "/should/not/use/this",
            },
            clear=True,
        ):
            env = launcher._scrubbed_env()

        assert set(env.keys()) == {"PATH", "HOME", "LANG", "PYTHONPATH"}
        assert env["PATH"] == "/usr/bin"
        assert env["HOME"] == "/home/test"
        assert env["LANG"] == "en_US.UTF-8"
        assert "SECRET_KEY" not in env
        assert "AWS_ACCESS_KEY_ID" not in env

    def test_pythonpath_derived_from_sys_path_not_os_environ(self) -> None:
        """PYTHONPATH is built from sys.path, ignoring os.environ['PYTHONPATH']."""
        launcher = _make_launcher()
        fake_sys_path = ["/app/src", "/app/lib", ""]

        with (
            patch.dict(
                os.environ,
                {"PATH": "/usr/bin", "HOME": "/home/test", "PYTHONPATH": "/bogus"},
                clear=True,
            ),
            patch.object(sys, "path", fake_sys_path),
        ):
            env = launcher._scrubbed_env()

        expected = os.pathsep.join(fake_sys_path)
        assert env["PYTHONPATH"] == expected
        assert "/bogus" not in env["PYTHONPATH"]

    def test_missing_optional_keys_omitted(self) -> None:
        """If LANG is not set in os.environ, it is absent from the result."""
        launcher = _make_launcher()

        with patch.dict(
            os.environ,
            {"PATH": "/usr/bin", "HOME": "/home/test"},
            clear=True,
        ):
            env = launcher._scrubbed_env()

        assert "LANG" not in env
        assert set(env.keys()) == {"PATH", "HOME", "PYTHONPATH"}


class TestCreateUdsDir:
    """Tests for SandboxLauncher._create_uds_dir."""

    def test_returns_dir_and_sock_path(self) -> None:
        """Returns (sock_dir, uds_path) where uds_path = sock_dir/sock."""
        launcher = _make_launcher()
        fake_dir = "/tmp/shu-sandbox-abc123"

        with patch("tempfile.mkdtemp", return_value=fake_dir):
            sock_dir, uds_path = launcher._create_uds_dir()

        assert sock_dir == fake_dir
        assert uds_path == os.path.join(fake_dir, "sock")


class TestBuildHandshakePayload:
    """Tests for SandboxLauncher._build_handshake_payload."""

    def test_shape_and_sorted_capabilities(self) -> None:
        """Payload has expected keys; capabilities are sorted deterministically."""
        launcher = _make_launcher()
        host = _FakeHost(declared_caps={"http", "kb", "cursor", "identity"})
        exec_ctx = _base_mod.ExecuteContext(user_id="user-1", agent_key="agent-xyz")

        payload = launcher._build_handshake_payload(
            host=host,
            user_id="user-1",
            user_email="user@test.com",
            provider_identities={"github": {"token": "abc"}},
            plugin_module="plugins.test",
            plugin_class="TestPlugin",
            execution_id="exec-42",
            exec_ctx=exec_ctx,
        )

        assert payload["user_id"] == "user-1"
        assert payload["user_email"] == "user@test.com"
        assert payload["providers"] == {"github": {"token": "abc"}}
        assert payload["plugin_module"] == "plugins.test"
        assert payload["plugin_class"] == "TestPlugin"
        assert payload["execution_id"] == "exec-42"
        # agent_key must round-trip so the child can rebuild ExecuteContext
        assert payload["agent_key"] == "agent-xyz"
        # Capabilities come from the host's declared set, sorted
        assert payload["capabilities"] == sorted(
            ["http", "kb", "cursor", "identity"]
        )

    def test_none_providers_becomes_empty_dict(self) -> None:
        """When provider_identities is None, the payload uses an empty dict."""
        launcher = _make_launcher()
        host = _FakeHost(declared_caps=set())
        exec_ctx = _base_mod.ExecuteContext(user_id="u")

        payload = launcher._build_handshake_payload(
            host=host,
            user_id="u",
            user_email=None,
            provider_identities=None,
            plugin_module="m",
            plugin_class="C",
            execution_id=None,
            exec_ctx=exec_ctx,
        )

        assert payload["providers"] == {}
        assert payload["user_email"] is None
        assert payload["execution_id"] is None
        # Default agent_key on ExecuteContext is None and must propagate.
        assert payload["agent_key"] is None


class TestTerminateChild:
    """Tests for SandboxLauncher._terminate_child."""

    @pytest.mark.asyncio
    async def test_sigterm_then_kill_on_grace_timeout(self) -> None:
        """SIGTERM is sent first, then SIGKILL after grace period expires."""
        launcher = _make_launcher()
        process = _make_mock_process(returncode=None, stderr_data=b"oops")

        # First wait() (the grace period) times out; second wait() (post-kill) returns.
        process.wait = AsyncMock(
            side_effect=[asyncio.TimeoutError(), 137]
        )

        # Patch wait_for to propagate the TimeoutError from the inner wait
        original_wait_for = asyncio.wait_for

        async def fake_wait_for(coro, *, timeout):
            try:
                return await coro
            except asyncio.TimeoutError:
                raise

        with patch.object(asyncio, "wait_for", side_effect=fake_wait_for):
            stderr_tail = await launcher._terminate_child(process)

        process.send_signal.assert_called_once_with(signal.SIGTERM)
        process.kill.assert_called_once()
        assert stderr_tail == "oops"

    @pytest.mark.asyncio
    async def test_sigterm_sufficient_no_kill(self) -> None:
        """If the process exits within the grace period, kill() is not called."""
        launcher = _make_launcher()
        process = _make_mock_process(returncode=None, stderr_data=b"clean exit")
        process.wait = AsyncMock(return_value=0)

        stderr_tail = await launcher._terminate_child(process)

        process.send_signal.assert_called_once_with(signal.SIGTERM)
        process.kill.assert_not_called()
        assert stderr_tail == "clean exit"

    @pytest.mark.asyncio
    async def test_already_dead_process_is_noop(self) -> None:
        """If process.returncode is already set, no signals are sent."""
        launcher = _make_launcher()
        process = _make_mock_process(returncode=1, stderr_data=b"already dead")

        stderr_tail = await launcher._terminate_child(process)

        process.send_signal.assert_not_called()
        process.kill.assert_not_called()
        assert stderr_tail == "already dead"

    @pytest.mark.asyncio
    async def test_none_process_returns_empty_string(self) -> None:
        """If process is None, returns empty string without error."""
        launcher = _make_launcher()

        stderr_tail = await launcher._terminate_child(None)

        assert stderr_tail == ""

    @pytest.mark.asyncio
    async def test_process_lookup_error_on_sigterm(self) -> None:
        """If send_signal raises ProcessLookupError, skip kill and return stderr."""
        launcher = _make_launcher()
        process = _make_mock_process(returncode=None, stderr_data=b"vanished")
        process.send_signal.side_effect = ProcessLookupError()

        stderr_tail = await launcher._terminate_child(process)

        process.kill.assert_not_called()
        assert stderr_tail == "vanished"


class TestRunOrchestration:
    """Integration-style tests for the full run() method with mocked boundaries."""

    def _run_kwargs(self, **overrides: Any) -> dict[str, Any]:
        """Build default keyword args for SandboxLauncher.run()."""
        defaults: dict[str, Any] = {
            "plugin_module": "plugins.test_plugin",
            "plugin_class": "TestPlugin",
            "vparams": {"query": "test"},
            "exec_ctx": _base_mod.ExecuteContext(user_id="user-1"),
            "host": _FakeHost(declared_caps={"http"}),
            "user_id": "user-1",
            "user_email": "user@test.com",
            "provider_identities": None,
            "execution_id": "exec-99",
        }
        defaults.update(overrides)
        return defaults

    @pytest.mark.asyncio
    async def test_uds_socket_chmod_0600(self) -> None:
        """os.chmod is called on the UDS path with mode 0o600."""
        launcher = _make_launcher()
        fake_sock_dir = "/tmp/shu-sandbox-test"
        fake_uds_path = os.path.join(fake_sock_dir, "sock")

        mock_server = AsyncMock()
        mock_server.close = MagicMock()
        mock_server.wait_closed = AsyncMock()

        mock_process = _make_mock_process(returncode=0)

        # Simulate the child connecting immediately
        async def fake_start_unix_server(callback, *, path):
            return mock_server

        async def fake_spawn_child(uds_path):
            return mock_process

        # _await_connection returns reader/writer; _run_rpc returns a PluginResult dict
        mock_reader = AsyncMock()
        mock_writer = AsyncMock()

        with (
            patch.object(
                launcher,
                "_create_uds_dir",
                return_value=(fake_sock_dir, fake_uds_path),
            ),
            patch.object(asyncio, "start_unix_server", side_effect=fake_start_unix_server),
            patch.object(launcher, "_spawn_child", side_effect=fake_spawn_child),
            patch.object(
                launcher,
                "_await_connection",
                return_value=(mock_reader, mock_writer),
            ),
            patch.object(
                launcher,
                "_run_rpc",
                return_value=PluginResult.ok({"result": 42}),
            ),
            patch.object(os, "chmod") as mock_chmod,
            patch.object(_launcher_mod.shutil, "rmtree") as mock_rmtree,
        ):
            result = await launcher.run(**self._run_kwargs())

        mock_chmod.assert_called_once_with(fake_uds_path, 0o600)
        assert result.status == "success"

    @pytest.mark.asyncio
    async def test_sock_dir_cleaned_up_in_finally(self) -> None:
        """shutil.rmtree is called with the sock_dir even after normal completion."""
        launcher = _make_launcher()
        fake_sock_dir = "/tmp/shu-sandbox-cleanup"
        fake_uds_path = os.path.join(fake_sock_dir, "sock")

        mock_server = AsyncMock()
        mock_server.close = MagicMock()
        mock_server.wait_closed = AsyncMock()

        mock_process = _make_mock_process(returncode=0)

        async def fake_start_unix_server(callback, *, path):
            return mock_server

        async def fake_spawn_child(uds_path):
            return mock_process

        mock_reader = AsyncMock()
        mock_writer = AsyncMock()

        with (
            patch.object(
                launcher,
                "_create_uds_dir",
                return_value=(fake_sock_dir, fake_uds_path),
            ),
            patch.object(asyncio, "start_unix_server", side_effect=fake_start_unix_server),
            patch.object(launcher, "_spawn_child", side_effect=fake_spawn_child),
            patch.object(
                launcher,
                "_await_connection",
                return_value=(mock_reader, mock_writer),
            ),
            patch.object(
                launcher,
                "_run_rpc",
                return_value=PluginResult.ok(),
            ),
            patch.object(os, "chmod"),
            patch.object(_launcher_mod.shutil, "rmtree") as mock_rmtree,
        ):
            await launcher.run(**self._run_kwargs())

        mock_rmtree.assert_called_once_with(fake_sock_dir, ignore_errors=True)

    @pytest.mark.asyncio
    async def test_sock_dir_cleaned_up_after_error(self) -> None:
        """shutil.rmtree is called with the sock_dir even after an exception."""
        launcher = _make_launcher()
        fake_sock_dir = "/tmp/shu-sandbox-err-cleanup"
        fake_uds_path = os.path.join(fake_sock_dir, "sock")

        mock_server = AsyncMock()
        mock_server.close = MagicMock()
        mock_server.wait_closed = AsyncMock()

        mock_process = _make_mock_process(returncode=None, stderr_data=b"boom")

        async def fake_start_unix_server(callback, *, path):
            return mock_server

        async def fake_spawn_child(uds_path):
            return mock_process

        with (
            patch.object(
                launcher,
                "_create_uds_dir",
                return_value=(fake_sock_dir, fake_uds_path),
            ),
            patch.object(asyncio, "start_unix_server", side_effect=fake_start_unix_server),
            patch.object(launcher, "_spawn_child", side_effect=fake_spawn_child),
            patch.object(
                launcher,
                "_await_connection",
                side_effect=RuntimeError("kaboom"),
            ),
            patch.object(os, "chmod"),
            patch.object(launcher, "_terminate_child", return_value="boom"),
            patch.object(_launcher_mod.shutil, "rmtree") as mock_rmtree,
        ):
            result = await launcher.run(**self._run_kwargs())

        mock_rmtree.assert_called_once_with(fake_sock_dir, ignore_errors=True)
        assert result.status == "error"

    @pytest.mark.asyncio
    async def test_pre_handshake_crash_returns_plugin_result_err(self) -> None:
        """Child exiting before UDS connect produces PluginResult.err with crash code."""
        launcher = _make_launcher()
        fake_sock_dir = "/tmp/shu-sandbox-crash"
        fake_uds_path = os.path.join(fake_sock_dir, "sock")

        mock_server = AsyncMock()
        mock_server.close = MagicMock()
        mock_server.wait_closed = AsyncMock()

        mock_process = _make_mock_process(returncode=1, stderr_data=b"some error")

        async def fake_start_unix_server(callback, *, path):
            return mock_server

        async def fake_spawn_child(uds_path):
            return mock_process

        with (
            patch.object(
                launcher,
                "_create_uds_dir",
                return_value=(fake_sock_dir, fake_uds_path),
            ),
            patch.object(asyncio, "start_unix_server", side_effect=fake_start_unix_server),
            patch.object(launcher, "_spawn_child", side_effect=fake_spawn_child),
            patch.object(
                launcher,
                "_await_connection",
                side_effect=_ChildCrashedBeforeConnect(
                    returncode=1, stderr_tail="some error"
                ),
            ),
            patch.object(os, "chmod"),
            patch.object(_launcher_mod.shutil, "rmtree"),
        ):
            result = await launcher.run(**self._run_kwargs())

        assert result.status == "error"
        assert result.error["code"] == "plugin_sandbox_child_crashed"
        assert "some error" in result.error["details"]["stderr_tail"]
        assert result.error["details"]["returncode"] == 1

    @pytest.mark.asyncio
    async def test_timeout_returns_plugin_result_err(self) -> None:
        """Timeout during RPC produces PluginResult.err with timeout code."""
        launcher = _make_launcher(timeout=5)
        fake_sock_dir = "/tmp/shu-sandbox-timeout"
        fake_uds_path = os.path.join(fake_sock_dir, "sock")

        mock_server = AsyncMock()
        mock_server.close = MagicMock()
        mock_server.wait_closed = AsyncMock()

        mock_process = _make_mock_process(returncode=None, stderr_data=b"timed out")

        async def fake_start_unix_server(callback, *, path):
            return mock_server

        async def fake_spawn_child(uds_path):
            return mock_process

        mock_reader = AsyncMock()
        mock_writer = AsyncMock()

        with (
            patch.object(
                launcher,
                "_create_uds_dir",
                return_value=(fake_sock_dir, fake_uds_path),
            ),
            patch.object(asyncio, "start_unix_server", side_effect=fake_start_unix_server),
            patch.object(launcher, "_spawn_child", side_effect=fake_spawn_child),
            patch.object(
                launcher,
                "_await_connection",
                return_value=(mock_reader, mock_writer),
            ),
            patch.object(
                launcher,
                "_run_rpc",
                side_effect=asyncio.TimeoutError(),
            ),
            patch.object(os, "chmod"),
            patch.object(launcher, "_terminate_child", return_value="timed out"),
            patch.object(_launcher_mod.shutil, "rmtree"),
        ):
            result = await launcher.run(**self._run_kwargs())

        assert result.status == "error"
        assert result.error["code"] == "plugin_sandbox_timeout"
        assert "5s" in result.error["message"]
        assert result.error["details"]["stderr_tail"] == "timed out"

    @pytest.mark.asyncio
    async def test_transport_error_returns_plugin_result_err(self) -> None:
        """ConnectionError during run produces PluginResult.err with transport code."""
        launcher = _make_launcher()
        fake_sock_dir = "/tmp/shu-sandbox-conn-err"
        fake_uds_path = os.path.join(fake_sock_dir, "sock")

        mock_server = AsyncMock()
        mock_server.close = MagicMock()
        mock_server.wait_closed = AsyncMock()

        mock_process = _make_mock_process(returncode=None, stderr_data=b"conn err")

        async def fake_start_unix_server(callback, *, path):
            return mock_server

        async def fake_spawn_child(uds_path):
            return mock_process

        mock_reader = AsyncMock()
        mock_writer = AsyncMock()

        with (
            patch.object(
                launcher,
                "_create_uds_dir",
                return_value=(fake_sock_dir, fake_uds_path),
            ),
            patch.object(asyncio, "start_unix_server", side_effect=fake_start_unix_server),
            patch.object(launcher, "_spawn_child", side_effect=fake_spawn_child),
            patch.object(
                launcher,
                "_await_connection",
                return_value=(mock_reader, mock_writer),
            ),
            patch.object(
                launcher,
                "_run_rpc",
                side_effect=ConnectionError("pipe broke"),
            ),
            patch.object(os, "chmod"),
            patch.object(launcher, "_terminate_child", return_value="conn err"),
            patch.object(_launcher_mod.shutil, "rmtree"),
        ):
            result = await launcher.run(**self._run_kwargs())

        assert result.status == "error"
        assert result.error["code"] == "plugin_sandbox_transport_error"
        assert "pipe broke" in result.error["message"]

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "exc_builder",
        [
            # Business exceptions from the plugin must bubble up unchanged so
            # the executor can translate them into structured PluginResult.err
            # payloads (provider_error, retry_after, capability names, etc.).
            # Regression test: the generic catch-all previously swallowed
            # these into plugin_sandbox_plugin_error, stripping context.
            lambda: _host_exc_mod.HttpRequestFailed(
                503, "https://api.example.com", body=None, headers={},
            ),
            lambda: _host_exc_mod.CapabilityDenied("secrets"),
            lambda: _host_exc_mod.EgressDenied("blocked.example.com"),
        ],
        ids=["HttpRequestFailed", "CapabilityDenied", "EgressDenied"],
    )
    async def test_business_exceptions_propagate_unchanged(self, exc_builder) -> None:
        launcher = _make_launcher()
        fake_sock_dir = "/tmp/shu-sandbox-biz-exc"
        fake_uds_path = os.path.join(fake_sock_dir, "sock")

        mock_server = AsyncMock()
        mock_server.close = MagicMock()
        mock_server.wait_closed = AsyncMock()

        mock_process = _make_mock_process(returncode=None, stderr_data=b"")

        async def fake_start_unix_server(callback, *, path):
            return mock_server

        async def fake_spawn_child(uds_path):
            return mock_process

        mock_reader = AsyncMock()
        mock_writer = AsyncMock()
        exc = exc_builder()

        with (
            patch.object(
                launcher,
                "_create_uds_dir",
                return_value=(fake_sock_dir, fake_uds_path),
            ),
            patch.object(asyncio, "start_unix_server", side_effect=fake_start_unix_server),
            patch.object(launcher, "_spawn_child", side_effect=fake_spawn_child),
            patch.object(
                launcher,
                "_await_connection",
                return_value=(mock_reader, mock_writer),
            ),
            patch.object(launcher, "_run_rpc", side_effect=exc),
            patch.object(os, "chmod"),
            patch.object(launcher, "_terminate_child", return_value="") as mock_term,
            patch.object(_launcher_mod.shutil, "rmtree"),
        ):
            with pytest.raises(type(exc)) as exc_info:
                await launcher.run(**self._run_kwargs())

        assert exc_info.value is exc
        # The child must still be terminated on the way out (once in the
        # except branch, and potentially again via _cleanup's finally if
        # the returncode is still unset — both paths call _terminate_child
        # with the process).
        assert mock_term.call_count >= 1
        for c in mock_term.call_args_list:
            assert c == call(mock_process)


class TestAwaitConnection:
    """Tests for _await_connection — the asyncio.wait race between child exit and UDS connect."""

    @pytest.mark.asyncio
    async def test_child_exit_before_connect_raises(self) -> None:
        """If the child exits before connecting, _ChildCrashedBeforeConnect is raised."""
        launcher = _make_launcher(timeout=10)
        process = _make_mock_process(returncode=1, stderr_data=b"import error")
        process.wait = AsyncMock(return_value=1)

        loop = asyncio.get_running_loop()
        connection_ready: asyncio.Future[tuple] = loop.create_future()

        with pytest.raises(_ChildCrashedBeforeConnect) as exc_info:
            await launcher._await_connection(
                connection_ready, process, "plugins.broken", timeout=10,
            )

        assert exc_info.value.returncode == 1
        assert "import error" in exc_info.value.stderr_tail

    @pytest.mark.asyncio
    async def test_successful_connect(self) -> None:
        """If the child connects before exiting, returns (reader, writer)."""
        launcher = _make_launcher(timeout=10)
        process = _make_mock_process(returncode=None)

        # process.wait() must block forever (child hasn't exited).
        # An AsyncMock with side_effect that awaits a never-resolved future
        # makes the create_task wrapper hang indefinitely.
        hang_forever = asyncio.get_running_loop().create_future()

        async def _block_forever():
            return await hang_forever

        process.wait = _block_forever

        loop = asyncio.get_running_loop()
        connection_ready: asyncio.Future[tuple] = loop.create_future()
        mock_reader = MagicMock()
        mock_writer = MagicMock()

        # Simulate child connecting immediately
        connection_ready.set_result((mock_reader, mock_writer))

        reader, writer = await launcher._await_connection(
            connection_ready, process, "plugins.ok", timeout=10,
        )

        assert reader is mock_reader
        assert writer is mock_writer


class TestReadStderrTail:
    """Tests for SandboxLauncher._read_stderr_tail."""

    @pytest.mark.asyncio
    async def test_truncates_to_limit(self) -> None:
        """Long stderr is truncated to _STDERR_TAIL_BYTES from the end."""
        launcher = _make_launcher()
        long_data = b"x" * 5000
        process = _make_mock_process(stderr_data=long_data)

        result = await launcher._read_stderr_tail(process)

        assert len(result) == 2000
        assert result == "x" * 2000

    @pytest.mark.asyncio
    async def test_none_process_returns_empty(self) -> None:
        """None process returns empty string."""
        launcher = _make_launcher()

        result = await launcher._read_stderr_tail(None)

        assert result == ""

    @pytest.mark.asyncio
    async def test_none_stderr_returns_empty(self) -> None:
        """Process with no stderr pipe returns empty string."""
        launcher = _make_launcher()
        process = _make_mock_process()
        process.stderr = None

        result = await launcher._read_stderr_tail(process)

        assert result == ""


class TestCleanup:
    """Tests for SandboxLauncher._cleanup."""

    @pytest.mark.asyncio
    async def test_closes_server_and_removes_dir(self) -> None:
        """Server is closed and sock_dir is removed."""
        launcher = _make_launcher()

        mock_server = MagicMock()
        mock_server.close = MagicMock()
        mock_server.wait_closed = AsyncMock()

        process = _make_mock_process(returncode=0)

        with patch.object(_launcher_mod.shutil, "rmtree") as mock_rmtree:
            await launcher._cleanup(mock_server, process, "/tmp/shu-sandbox-clean")

        mock_server.close.assert_called_once()
        mock_rmtree.assert_called_once_with(
            "/tmp/shu-sandbox-clean", ignore_errors=True
        )

    @pytest.mark.asyncio
    async def test_terminates_still_running_process(self) -> None:
        """If process.returncode is None, _terminate_child is called."""
        launcher = _make_launcher()
        process = _make_mock_process(returncode=None)

        with (
            patch.object(
                launcher, "_terminate_child", return_value=""
            ) as mock_terminate,
            patch.object(_launcher_mod.shutil, "rmtree"),
        ):
            await launcher._cleanup(None, process, "/tmp/shu-sandbox-term")

        mock_terminate.assert_called_once_with(process)

    @pytest.mark.asyncio
    async def test_none_server_and_process_no_error(self) -> None:
        """Cleanup handles None server and None process gracefully."""
        launcher = _make_launcher()

        with patch.object(_launcher_mod.shutil, "rmtree") as mock_rmtree:
            await launcher._cleanup(None, None, "/tmp/shu-sandbox-none")

        mock_rmtree.assert_called_once_with(
            "/tmp/shu-sandbox-none", ignore_errors=True
        )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
