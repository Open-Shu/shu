"""Unit tests for the sandbox import deny list and filesystem guards."""

from __future__ import annotations

import asyncio
import builtins
import importlib.machinery
import io
import os
import pathlib
import sys
import tempfile
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from _module_loader import load_module as _load_module

_SANDBOX_DIR = Path(__file__).resolve().parents[4] / "shu" / "plugins" / "sandbox"
_HOST_DIR = Path(__file__).resolve().parents[4] / "shu" / "plugins" / "host"

if "shu" not in sys.modules:
    sys.modules["shu"] = MagicMock()
if "shu.plugins" not in sys.modules:
    sys.modules["shu.plugins"] = MagicMock()
if "shu.plugins.host" not in sys.modules:
    sys.modules["shu.plugins.host"] = MagicMock()
if "shu.plugins.sandbox" not in sys.modules:
    sys.modules["shu.plugins.sandbox"] = MagicMock()

# child_bootstrap loads host modules by file path internally, but it also
# imports sandbox siblings (exceptions, rpc, etc.) via normal imports.
# Pre-load the dependency chain so the direct-load of child_bootstrap works.
_load_module("shu.plugins.host.exceptions", _HOST_DIR / "exceptions.py")
_load_module("shu.plugins.host.base", _HOST_DIR / "base.py")
_load_module("shu.plugins.host.identity_capability", _HOST_DIR / "identity_capability.py")
_load_module("shu.plugins.host.log_capability", _HOST_DIR / "log_capability.py")
_load_module("shu.plugins.host.utils_capability", _HOST_DIR / "utils_capability.py")

_load_module("shu.plugins.sandbox.rpc", _SANDBOX_DIR / "rpc.py")
_load_module("shu.plugins.sandbox.exceptions", _SANDBOX_DIR / "exceptions.py")
_load_module("shu.plugins.sandbox.rpc_client", _SANDBOX_DIR / "rpc_client.py")
_load_module("shu.plugins.sandbox.proxy_host", _SANDBOX_DIR / "proxy_host.py")
_load_module("shu.plugins.sandbox.logging_ferry", _SANDBOX_DIR / "logging_ferry.py")

# shu.plugins.base is needed by child_bootstrap for ExecuteContext/PluginResult.
_PLUGINS_DIR = Path(__file__).resolve().parents[4] / "shu" / "plugins"
_load_module("shu.plugins.base", _PLUGINS_DIR / "base.py")

_bootstrap_mod = _load_module(
    "shu.plugins.sandbox.child_bootstrap", _SANDBOX_DIR / "child_bootstrap.py",
)

_is_denied = _bootstrap_mod._is_denied
_SandboxedFinder = _bootstrap_mod._SandboxedFinder
install_import_guard = _bootstrap_mod.install_import_guard
install_fs_guard = _bootstrap_mod.install_fs_guard
install_spawn_guard = _bootstrap_mod.install_spawn_guard
install_loader_guard = _bootstrap_mod.install_loader_guard
install_asyncio_guard = _bootstrap_mod.install_asyncio_guard
_DENIED_MODULES = _bootstrap_mod._DENIED_MODULES
_SPAWN_FUNCTIONS = _bootstrap_mod._SPAWN_FUNCTIONS
_ASYNCIO_MODULE_FUNCTIONS = _bootstrap_mod._ASYNCIO_MODULE_FUNCTIONS
_ASYNCIO_LOOP_METHODS = _bootstrap_mod._ASYNCIO_LOOP_METHODS


class TestIsDenied:
    @pytest.mark.parametrize("name", [
        "ctypes",
        "socket",
        "ssl",
        "subprocess",
        "shutil",
        "requests",
        "httpx",
        "urllib",
        "urllib3",
        "http.client",
        "shu",
    ])
    def test_exact_match_denied(self, name: str):
        assert _is_denied(name) is True

    @pytest.mark.parametrize("name", [
        "ctypes.util",
        "socket.error",
        "ssl.SSLContext",
        "subprocess.Popen",
        "shutil.copy",
        "requests.api",
        "httpx._client",
        "urllib.request",
        "urllib.parse",
        "urllib3.poolmanager",
        "urllib3.contrib.pyopenssl",
        "http.client.HTTPConnection",
        "shu.core.config",
        "shu.plugins.host.http_capability",
        "shu.services.ingestion_service",
    ])
    def test_submodule_denied(self, name: str):
        assert _is_denied(name) is True

    @pytest.mark.parametrize("name", [
        "json",
        "os",
        "asyncio",
        "dataclasses",
        "collections",
        "re",
        "math",
        "datetime",
        "http.server",  # only http.client is denied, not all of http
        "http",
        "logging",
    ])
    def test_allowed(self, name: str):
        assert _is_denied(name) is False


class TestSandboxedFinder:
    def test_find_spec_raises_on_denied(self):
        finder = _SandboxedFinder()
        with pytest.raises(ImportError, match="blocked in the plugin sandbox"):
            finder.find_spec("socket")

    def test_find_spec_raises_on_denied_submodule(self):
        finder = _SandboxedFinder()
        with pytest.raises(ImportError, match="blocked in the plugin sandbox"):
            finder.find_spec("shu.core.config")

    def test_find_spec_returns_none_for_allowed(self):
        finder = _SandboxedFinder()
        assert finder.find_spec("json") is None
        assert finder.find_spec("asyncio") is None

    def test_find_module_returns_none(self):
        """find_module is deprecated; our implementation always returns None
        and lets find_spec handle the check."""
        finder = _SandboxedFinder()
        assert finder.find_module("socket") is None


class TestInstallImportGuard:
    """install_import_guard now patches sys.meta_path, sys.modules,
    importlib.import_module, and builtins.__import__. The autouse
    fixture snapshots all four and restores them so test pollution
    doesn't break the test runner itself (which needs imports to work).
    """

    @pytest.fixture(autouse=True)
    def _save_restore_import_state(self):
        import importlib  # noqa: PLC0415
        orig_meta_path = list(sys.meta_path)
        orig_sys_modules = dict(sys.modules)
        orig_import_module = importlib.import_module
        orig_builtin_import = builtins.__import__
        yield
        sys.meta_path[:] = orig_meta_path
        # Restore any scrubbed entries, but don't remove new ones added
        # by other code during the test.
        for k, v in orig_sys_modules.items():
            sys.modules.setdefault(k, v)
        importlib.import_module = orig_import_module
        builtins.__import__ = orig_builtin_import

    def test_installs_at_head_of_meta_path(self):
        original_len = len(sys.meta_path)
        install_import_guard()
        assert len(sys.meta_path) == original_len + 1
        assert isinstance(sys.meta_path[0], _SandboxedFinder)

    def test_guard_blocks_import_after_install(self):
        install_import_guard()
        with pytest.raises(ImportError, match="blocked in the plugin sandbox"):
            # Use a submodule that is never pre-loaded so no other
            # finder short-circuits before ours.
            import shu.nonexistent_module  # noqa: F401

    def test_scrubs_denied_entries_from_sys_modules(self):
        """Regression: a denied module already cached in sys.modules must
        not satisfy a plugin's ``import urllib`` call."""
        # Ensure urllib is cached before install.
        import urllib  # noqa: F401, PLC0415
        assert "urllib" in sys.modules
        install_import_guard()
        assert "urllib" not in sys.modules, (
            "install_import_guard must scrub denied entries from sys.modules"
        )

    def test_scrubs_denied_submodule_entries(self):
        """Submodules of denied packages (urllib.parse, etc.) must also
        be scrubbed — otherwise ``import urllib.parse`` returns the cache."""
        import urllib.parse  # noqa: F401, PLC0415
        assert "urllib.parse" in sys.modules
        install_import_guard()
        assert "urllib.parse" not in sys.modules

    def test_importlib_import_module_rejects_denied(self):
        """Defense in depth: importlib.import_module("urllib") must raise
        after install_import_guard, even if urllib is re-cached."""
        import importlib  # noqa: PLC0415
        install_import_guard()
        with pytest.raises(ImportError, match="blocked in the plugin sandbox"):
            importlib.import_module("urllib")

    def test_builtin_import_rejects_denied(self):
        """Defense in depth: the ``import`` statement form also rejects."""
        install_import_guard()
        with pytest.raises(ImportError, match="blocked in the plugin sandbox"):
            builtins.__import__("urllib3")


class TestDenyListCompleteness:
    def test_all_required_modules_present(self):
        """AC: deny list must contain all of these."""
        required = {
            "ctypes", "socket", "ssl", "subprocess", "shutil",
            "requests", "httpx", "urllib", "urllib3", "http.client", "shu",
            "sqlite3",
        }
        assert required == _DENIED_MODULES


class TestInstallFsGuard:
    """Tests for install_fs_guard.

    install_fs_guard replaces all three functions at once, so every test
    must save and restore all of them to avoid poisoning later tests.
    """

    @pytest.fixture(autouse=True)
    def _save_restore_fs_functions(self):
        orig_os_open = os.open
        orig_builtin_open = builtins.open
        orig_os_openat = getattr(os, "openat", None)
        orig_os_fdopen = os.fdopen
        orig_io_open = io.open
        orig_path_open = pathlib.Path.open
        yield
        os.open = orig_os_open
        builtins.open = orig_builtin_open
        if orig_os_openat is not None:
            os.openat = orig_os_openat
        os.fdopen = orig_os_fdopen
        io.open = orig_io_open
        pathlib.Path.open = orig_path_open

    def test_os_open_blocked(self):
        install_fs_guard()
        with pytest.raises(PermissionError, match="Sandbox blocks filesystem access"):
            os.open("/dev/null", os.O_RDONLY)

    def test_builtins_open_blocked(self):
        install_fs_guard()
        with pytest.raises(PermissionError, match="Sandbox blocks filesystem access"):
            open("/dev/null")  # noqa: SIM115

    def test_os_openat_blocked(self):
        if not hasattr(os, "openat"):
            pytest.skip("os.openat not available on this platform")
        install_fs_guard()
        with pytest.raises(PermissionError, match="Sandbox blocks filesystem access"):
            os.openat(-1, "/dev/null", os.O_RDONLY)

    def test_io_open_blocked(self):
        """Regression: pathlib/third-party libs often use io.open directly."""
        install_fs_guard()
        with pytest.raises(PermissionError, match="Sandbox blocks filesystem access"):
            io.open("/dev/null")  # noqa: SIM115

    def test_os_fdopen_blocked(self):
        """Regression: os.fdopen wraps an existing fd — a plugin could
        otherwise use an inherited fd (0/1/2) to read a file."""
        install_fs_guard()
        with pytest.raises(PermissionError, match="Sandbox blocks filesystem access"):
            os.fdopen(0, "rb")

    def test_pathlib_path_open_blocked(self):
        """Regression: Path.open is an attack surface independent of
        builtins.open (defense in depth against pathlib internals changes)."""
        install_fs_guard()
        with pytest.raises(PermissionError, match="Sandbox blocks filesystem access"):
            pathlib.Path("/dev/null").open("rb")

    def test_pathlib_read_text_blocked(self):
        """Path.read_text() goes through io.open — if io.open is stubbed,
        this must fail too (catches the .env / /proc/self/environ pattern)."""
        install_fs_guard()
        with pytest.raises(PermissionError, match="Sandbox blocks filesystem access"):
            pathlib.Path("/dev/null").read_text()

    def test_pathlib_read_bytes_blocked(self):
        install_fs_guard()
        with pytest.raises(PermissionError, match="Sandbox blocks filesystem access"):
            pathlib.Path("/dev/null").read_bytes()

    def test_originals_restored_by_fixture(self):
        install_fs_guard()
        with pytest.raises(PermissionError):
            os.open("/dev/null", os.O_RDONLY)


class TestInstallLoaderGuard:
    """Regression tests for the importlib loader bypass.

    Without install_loader_guard, a plugin could bypass the fs guard
    with::

        importlib.machinery.SourceFileLoader("x", "/etc/passwd").get_data(
            "/etc/passwd"
        )

    ``get_data`` uses low-level file primitives that don't go through
    ``open`` / ``io.open`` / ``pathlib.Path.open``.
    """

    @pytest.fixture(autouse=True)
    def _save_restore_loader_methods(self):
        originals = {
            cls: cls.get_data
            for cls in (
                importlib.machinery.SourceFileLoader,
                importlib.machinery.SourcelessFileLoader,
                importlib.machinery.ExtensionFileLoader,
            )
        }
        yield
        for cls, fn in originals.items():
            cls.get_data = fn

    def test_source_file_loader_get_data_blocked(self):
        install_loader_guard()
        loader = importlib.machinery.SourceFileLoader("x", "/etc/hosts")
        with pytest.raises(PermissionError, match="Sandbox blocks filesystem access"):
            loader.get_data("/etc/hosts")

    def test_sourceless_file_loader_get_data_blocked(self):
        install_loader_guard()
        loader = importlib.machinery.SourcelessFileLoader("x", "/etc/hosts")
        with pytest.raises(PermissionError, match="Sandbox blocks filesystem access"):
            loader.get_data("/etc/hosts")

    def test_extension_file_loader_get_data_blocked(self):
        install_loader_guard()
        loader = importlib.machinery.ExtensionFileLoader("x", "/dev/null")
        with pytest.raises(PermissionError, match="Sandbox blocks filesystem access"):
            loader.get_data("/etc/hosts")

    def test_arbitrary_path_still_blocked(self):
        """The guard is path-agnostic — any path passed to get_data fails."""
        install_loader_guard()
        loader = importlib.machinery.SourceFileLoader("x", "/tmp/nonexistent")
        with pytest.raises(PermissionError, match="Sandbox blocks filesystem access"):
            loader.get_data("/any/path/the/plugin/chooses")


class TestInstallSpawnGuard:
    """Tests for install_spawn_guard.

    Uses an autouse fixture to save/restore all os.* spawn functions so
    the test runner itself is not poisoned.
    """

    @pytest.fixture(autouse=True)
    def _save_restore_spawn_functions(self):
        originals = {
            name: getattr(os, name)
            for name in _SPAWN_FUNCTIONS
            if hasattr(os, name)
        }
        yield
        for name, fn in originals.items():
            setattr(os, name, fn)

    def test_all_spawn_functions_blocked(self):
        install_spawn_guard()
        for name in _SPAWN_FUNCTIONS:
            if not hasattr(os, name):
                continue
            with pytest.raises(PermissionError, match="Sandbox blocks process spawning"):
                getattr(os, name)("dummy_arg")

    def test_os_system_blocked(self):
        install_spawn_guard()
        with pytest.raises(PermissionError, match="Sandbox blocks process spawning"):
            os.system("echo hi")

    def test_os_fork_blocked(self):
        if not hasattr(os, "fork"):
            pytest.skip("os.fork not available on this platform")
        install_spawn_guard()
        with pytest.raises(PermissionError, match="Sandbox blocks process spawning"):
            os.fork()

    def test_os_popen_blocked(self):
        install_spawn_guard()
        with pytest.raises(PermissionError, match="Sandbox blocks process spawning"):
            os.popen("echo hi")

    def test_os_execv_blocked(self):
        install_spawn_guard()
        with pytest.raises(PermissionError, match="Sandbox blocks process spawning"):
            os.execv("/bin/echo", ["echo", "hi"])

    def test_os_posix_spawn_blocked(self):
        if not hasattr(os, "posix_spawn"):
            pytest.skip("os.posix_spawn not available on this platform")
        install_spawn_guard()
        with pytest.raises(PermissionError, match="Sandbox blocks process spawning"):
            os.posix_spawn("/bin/echo", ["echo"], {})

    def test_spawn_list_covers_ac(self):
        """AC requires all these function families are covered."""
        required = {
            "system", "popen",
            "execv", "execve", "execvp", "execvpe",
            "execl", "execle", "execlp", "execlpe",
            "fork", "forkpty",
            "spawnv", "spawnve", "spawnvp", "spawnvpe",
            "spawnl", "spawnle", "spawnlp", "spawnlpe",
            "posix_spawn", "posix_spawnp",
        }
        assert required == set(_SPAWN_FUNCTIONS)


class TestInstallAsyncioGuard:
    """Tests for install_asyncio_guard — the asyncio module and the
    running event loop expose network/subprocess primitives that are
    equivalent to ``socket`` / ``subprocess`` but live on a module that
    cannot be placed on the import deny list (asyncio is load-bearing).
    """

    @pytest.fixture(autouse=True)
    def _save_restore_asyncio_functions(self):
        import asyncio  # noqa: PLC0415
        orig_module = {
            name: getattr(asyncio, name)
            for name in _ASYNCIO_MODULE_FUNCTIONS
            if hasattr(asyncio, name)
        }
        # Class-level patches persist across tests unless restored, and
        # leak into the running pytest process — critical to snapshot.
        base_cls = asyncio.base_events.BaseEventLoop
        orig_loop_methods = {
            name: getattr(base_cls, name)
            for name in _ASYNCIO_LOOP_METHODS
            if hasattr(base_cls, name)
        }
        orig_new_event_loop = asyncio.new_event_loop
        yield
        for name, fn in orig_module.items():
            setattr(asyncio, name, fn)
        for name, fn in orig_loop_methods.items():
            setattr(base_cls, name, fn)
        asyncio.new_event_loop = orig_new_event_loop

    @pytest.mark.asyncio
    async def test_asyncio_open_connection_blocked(self):
        import asyncio  # noqa: PLC0415
        install_asyncio_guard()
        with pytest.raises(PermissionError, match="asyncio network/subprocess"):
            await asyncio.open_connection("127.0.0.1", 80)

    @pytest.mark.asyncio
    async def test_asyncio_open_unix_connection_blocked(self):
        import asyncio  # noqa: PLC0415
        install_asyncio_guard()
        with pytest.raises(PermissionError, match="asyncio network/subprocess"):
            await asyncio.open_unix_connection("/tmp/nowhere.sock")

    @pytest.mark.asyncio
    async def test_asyncio_create_subprocess_exec_blocked(self):
        import asyncio  # noqa: PLC0415
        install_asyncio_guard()
        with pytest.raises(PermissionError, match="asyncio network/subprocess"):
            await asyncio.create_subprocess_exec("/bin/echo", "hi")

    @pytest.mark.asyncio
    async def test_asyncio_create_subprocess_shell_blocked(self):
        import asyncio  # noqa: PLC0415
        install_asyncio_guard()
        with pytest.raises(PermissionError, match="asyncio network/subprocess"):
            await asyncio.create_subprocess_shell("echo hi")

    @pytest.mark.asyncio
    async def test_loop_subprocess_exec_blocked(self):
        """Regression: even if a plugin grabs the loop directly, the
        low-level loop methods must also be stubbed."""
        import asyncio  # noqa: PLC0415
        loop = asyncio.get_running_loop()
        install_asyncio_guard()
        with pytest.raises(PermissionError, match="asyncio network/subprocess"):
            await loop.subprocess_exec(
                asyncio.SubprocessProtocol, "/bin/echo", "hi",
            )

    @pytest.mark.asyncio
    async def test_loop_create_connection_blocked(self):
        import asyncio  # noqa: PLC0415
        loop = asyncio.get_running_loop()
        install_asyncio_guard()
        with pytest.raises(PermissionError, match="asyncio network/subprocess"):
            await loop.create_connection(asyncio.Protocol, "127.0.0.1", 80)

    @pytest.mark.asyncio
    async def test_new_event_loop_blocked(self):
        """Fresh-loop bypass: plugin cannot obtain an unpatched loop
        via asyncio.new_event_loop()."""
        import asyncio  # noqa: PLC0415
        install_asyncio_guard()
        with pytest.raises(PermissionError, match="asyncio network/subprocess"):
            asyncio.new_event_loop()

    @pytest.mark.asyncio
    async def test_loop_method_patch_is_class_level(self):
        """Patching BaseEventLoop at the class means even a loop
        constructed before install_asyncio_guard() runs inherits the
        patched method via MRO."""
        import asyncio  # noqa: PLC0415
        # Snapshot a raw loop reference before guard installs. Once the
        # guard is up, asyncio.new_event_loop() is blocked, so this
        # captures the only available pre-patch construction path.
        orig_new_event_loop = asyncio.new_event_loop
        pre_guard_loop = orig_new_event_loop()
        try:
            install_asyncio_guard()
            with pytest.raises(PermissionError, match="asyncio network/subprocess"):
                await pre_guard_loop.create_connection(
                    asyncio.Protocol, "127.0.0.1", 80,
                )
        finally:
            pre_guard_loop.close()

    def test_asyncio_function_list_covers_core_surface(self):
        required = {
            "open_connection", "open_unix_connection",
            "start_server", "start_unix_server",
            "create_subprocess_exec", "create_subprocess_shell",
        }
        assert required == set(_ASYNCIO_MODULE_FUNCTIONS)

    def test_loop_method_list_covers_core_surface(self):
        required = {
            "create_connection", "create_unix_connection",
            "create_server", "create_unix_server",
            "create_datagram_endpoint",
            "connect_accepted_socket",
            "connect_read_pipe", "connect_write_pipe",
            "sock_connect",
            "subprocess_exec", "subprocess_shell",
        }
        assert required == set(_ASYNCIO_LOOP_METHODS)


_BACKEND_SRC = str(Path(__file__).resolve().parents[4])
_SUBPROCESS_TIMEOUT = 5.0


def _write_test_plugin(tmp_path: Path, execute_body: str) -> tuple[str, str, str]:
    """Write a minimal adversarial plugin to *tmp_path* and return import info.

    Returns ``(tmp_dir_str, module_name, class_name)`` where *tmp_dir_str*
    must be added to ``PYTHONPATH`` for the subprocess to find the module.
    """
    pkg_dir = tmp_path / "adversarial_plugin"
    pkg_dir.mkdir(parents=True, exist_ok=True)

    # The plugin cannot import from shu.* (the import guard blocks it).
    # It satisfies the Plugin Protocol structurally via duck-typing.
    # _FakeResult provides a .model_dump() compatible with PluginResult
    # for tests that need to return a result.
    plugin_src = (
        "\n"
        "class _FakeResult:\n"
        "    def __init__(self, status, data=None, error=None,\n"
        "                 warnings=None, citations=None):\n"
        "        self.status = status\n"
        "        self.data = data\n"
        "        self.error = error\n"
        "        self.warnings = warnings\n"
        "        self.citations = citations\n"
        "\n"
        "    def model_dump(self):\n"
        "        return {\n"
        "            'status': self.status,\n"
        "            'data': self.data,\n"
        "            'error': self.error,\n"
        "            'warnings': self.warnings,\n"
        "            'citations': self.citations,\n"
        "        }\n"
        "\n"
        "\n"
        "class AdversarialPlugin:\n"
        "    name = 'adversarial'\n"
        "    version = '0.0.1'\n"
        "\n"
        "    def get_schema(self):\n"
        "        return None\n"
        "\n"
        "    def get_schema_for_op(self, op):\n"
        "        return None\n"
        "\n"
        "    def get_output_schema(self):\n"
        "        return None\n"
        "\n"
        "    async def execute(self, params, context, host):\n"
    )
    for line in execute_body.strip().splitlines():
        plugin_src += "        " + line + "\n"

    (pkg_dir / "plugin.py").write_text(plugin_src)
    (pkg_dir / "__init__.py").write_text(
        "from adversarial_plugin.plugin import AdversarialPlugin\n"
    )

    return str(tmp_path), "adversarial_plugin.plugin", "AdversarialPlugin"


async def _run_adversarial_plugin(
    execute_body: str,
    tmp_path: Path,
    extra_env: dict[str, str] | None = None,
    timeout: float = _SUBPROCESS_TIMEOUT,
) -> dict[str, Any]:
    """Spawn a real child_bootstrap subprocess against an adversarial plugin.

    Acts as a fake parent: accepts the UDS connection from the child,
    sends a handshake pointing to the adversarial plugin, waits for
    MSG_READY, sends MSG_EXECUTE, and collects the final message.

    Returns the final frame dict (``MSG_FINAL_RESULT`` or ``MSG_FINAL_ERROR``).
    Raises ``asyncio.TimeoutError`` if the child does not send a final
    message within *timeout* seconds.
    """
    from shu.plugins.sandbox.rpc import (
        MSG_FINAL_ERROR,
        MSG_FINAL_RESULT,
        MSG_HANDSHAKE,
        MSG_LOG,
        MSG_READY,
        ParentMessage,
        read_frame,
        write_frame,
    )

    tmp_dir_str, plugin_module, plugin_class = _write_test_plugin(tmp_path, execute_body)

    sock_dir = tempfile.mkdtemp(prefix="shu_adv_")
    uds_path = os.path.join(sock_dir, "p.sock")

    final_frame: dict[str, Any] | None = None
    child_connected = asyncio.Event()

    async def _handle_client(
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        nonlocal final_frame
        try:
            handshake_payload = {
                "plugin_module": plugin_module,
                "plugin_class": plugin_class,
                "user_id": "test-user",
                "user_email": "test@example.com",
                "providers": {},
                "capabilities": [],
            }
            await write_frame(writer, ParentMessage.handshake(handshake_payload))

            child_connected.set()

            # Drain frames until we get a final message. Ignore MSG_LOG
            # and MSG_READY frames; MSG_CALL gets a stub error response.
            while True:
                frame = await read_frame(reader)
                msg_type = frame.get("type")
                if msg_type == MSG_READY:
                    await write_frame(
                        writer,
                        ParentMessage.execute(vparams={}),
                    )
                elif msg_type in (MSG_FINAL_RESULT, MSG_FINAL_ERROR):
                    final_frame = frame
                    return
                elif msg_type == MSG_LOG:
                    pass
                elif msg_type == "call":
                    # Stub: respond with an error so the child doesn't hang
                    await write_frame(
                        writer,
                        ParentMessage.error(
                            id=frame.get("id", 0),
                            exc_payload={
                                "exc_type": "PluginError",
                                "payload": {"message": "stub"},
                            },
                        ),
                    )
        except (asyncio.IncompleteReadError, ConnectionError, OSError):
            pass
        finally:
            writer.close()

    server = await asyncio.start_unix_server(_handle_client, path=uds_path)

    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": os.environ.get("HOME", "/tmp"),
        "LANG": os.environ.get("LANG", "en_US.UTF-8"),
        "PYTHONPATH": f"{_BACKEND_SRC}:{tmp_dir_str}",
    }
    if extra_env:
        env.update(extra_env)

    proc = await asyncio.create_subprocess_exec(
        sys.executable, "-m", "shu.plugins.sandbox.child_bootstrap", uds_path,
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    try:
        async def _wait_for_final() -> dict[str, Any]:
            # The server callback populates final_frame when it arrives
            await proc.wait()
            # Give the handler a moment to finish if the process exited
            # after writing the final frame
            await asyncio.sleep(0.1)
            if final_frame is not None:
                return final_frame
            raise RuntimeError(
                f"Child exited (rc={proc.returncode}) without sending a final message"
            )

        return await asyncio.wait_for(_wait_for_final(), timeout=timeout)
    finally:
        if proc.returncode is None:
            proc.terminate()
            try:
                await asyncio.wait_for(proc.wait(), timeout=2.0)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
        server.close()
        await server.wait_closed()
        try:
            os.unlink(uds_path)
        except FileNotFoundError:
            pass
        try:
            os.rmdir(sock_dir)
        except OSError:
            pass


class TestAdversarialPlugins:
    """Real-subprocess adversarial tests for child_bootstrap.

    Each test spawns ``python -m shu.plugins.sandbox.child_bootstrap``
    against a tiny test plugin that attempts a specific sandbox bypass.
    A fake parent on the other side of the UDS collects the final
    message and the tests assert the expected sandbox denial or
    exception-type preservation.

    These tests are slow (0.5-3 s each) because each spawns a real
    Python subprocess.
    """

    @pytest.mark.asyncio
    async def test_scrubbed_environment_hides_secrets(self, tmp_path: Path) -> None:
        """Environment variables not explicitly passed are invisible to the plugin."""
        execute_body = """\
import os
val = os.environ.get("SHU_DATABASE_URL")
return _FakeResult(status="success", data={"db_url": val})
"""
        result = await _run_adversarial_plugin(execute_body, tmp_path)
        assert result["type"] == "final_result"
        assert result["value"]["data"]["db_url"] is None

    @pytest.mark.asyncio
    async def test_open_dotenv_blocked(self, tmp_path: Path) -> None:
        """``open('.env')`` is blocked by the filesystem guard."""
        execute_body = """\
open(".env").read()
"""
        result = await _run_adversarial_plugin(execute_body, tmp_path)
        assert result["type"] == "final_error"
        # PermissionError is not in SERIALIZABLE, so it collapses to PluginError
        assert result["exc_type"] == "PluginError"
        assert result["payload"]["original_type"] == "PermissionError"
        assert "Sandbox blocks filesystem access" in result["traceback"]

    @pytest.mark.asyncio
    async def test_open_etc_passwd_blocked(self, tmp_path: Path) -> None:
        """``open('/etc/passwd')`` is blocked by the filesystem guard."""
        execute_body = """\
open("/etc/passwd").read()
"""
        result = await _run_adversarial_plugin(execute_body, tmp_path)
        assert result["type"] == "final_error"
        assert result["exc_type"] == "PluginError"
        assert result["payload"]["original_type"] == "PermissionError"
        assert "Sandbox blocks filesystem access" in result["traceback"]

    @pytest.mark.asyncio
    async def test_os_open_proc_environ_blocked(self, tmp_path: Path) -> None:
        """``os.open('/proc/self/environ', ...)`` is blocked by the filesystem guard.

        On macOS ``/proc`` does not exist, but the fs guard fires before
        the syscall reaches the kernel — ``os.open`` is replaced with a
        raising stub — so this works portably.
        """
        execute_body = """\
import os
os.open("/proc/self/environ", os.O_RDONLY)
"""
        result = await _run_adversarial_plugin(execute_body, tmp_path)
        assert result["type"] == "final_error"
        assert result["exc_type"] == "PluginError"
        assert result["payload"]["original_type"] == "PermissionError"
        assert "Sandbox blocks filesystem access" in result["traceback"]

    @pytest.mark.asyncio
    async def test_import_ctypes_blocked(self, tmp_path: Path) -> None:
        """``import ctypes`` inside execute is blocked by the import guard.

        The import is placed inside execute (not at module top level) so
        the import guard is already installed when it fires.
        """
        execute_body = """\
import ctypes
"""
        result = await _run_adversarial_plugin(execute_body, tmp_path)
        assert result["type"] == "final_error"
        # ImportError is not in SERIALIZABLE, so it collapses to PluginError
        assert result["exc_type"] == "PluginError"
        assert result["payload"]["original_type"] == "ImportError"
        assert "blocked in the plugin sandbox" in result["traceback"]

    @pytest.mark.asyncio
    async def test_os_system_blocked(self, tmp_path: Path) -> None:
        """``os.system('id')`` is blocked by the spawn guard."""
        execute_body = """\
import os
os.system("id")
"""
        result = await _run_adversarial_plugin(execute_body, tmp_path)
        assert result["type"] == "final_error"
        assert result["exc_type"] == "PluginError"
        assert result["payload"]["original_type"] == "PermissionError"
        assert "Sandbox blocks process spawning" in result["traceback"]

    @pytest.mark.asyncio
    async def test_infinite_loop_killed_by_timeout(self, tmp_path: Path) -> None:
        """A plugin that loops forever is killed by the test-level timeout.

        The launcher (task 23) enforces timeouts in production; this test
        verifies the subprocess can be terminated externally when it hangs.
        """
        execute_body = """\
while True:
    pass
"""
        with pytest.raises((asyncio.TimeoutError, RuntimeError)):
            await _run_adversarial_plugin(execute_body, tmp_path, timeout=3.0)

    @pytest.mark.asyncio
    async def test_value_error_roundtrips_as_value_error(self, tmp_path: Path) -> None:
        """``ValueError`` IS in SERIALIZABLE, so it round-trips with its
        original type preserved (not collapsed to PluginError).
        """
        execute_body = """\
raise ValueError("boom")
"""
        result = await _run_adversarial_plugin(execute_body, tmp_path)
        assert result["type"] == "final_error"
        assert result["exc_type"] == "ValueError"
        assert result["payload"]["message"] == "boom"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
