"""Entry point for the sandbox subprocess.

Executed as ``python -m shu.plugins.sandbox.child_bootstrap <uds_path>``.
The module handles lockdown ordering so each guard is installed only when
it is safe to do so.  This file is intentionally kept as a flat script
rather than a class hierarchy — the execution order matters and must be
readable top-to-bottom.

All ``shu.*`` imports happen here at module level, *before*
``install_import_guard()`` blocks them.  Once the guard is active the
only importable modules are stdlib + third-party plugin dependencies.
"""

from __future__ import annotations

import asyncio
import builtins
import importlib
import importlib.machinery
import io
import os
import pathlib
import sys
import traceback
from importlib.abc import MetaPathFinder
from importlib.machinery import ModuleSpec
from types import ModuleType
from typing import Any

from shu.plugins.base import ExecuteContext, PluginResult
from shu.plugins.host.identity_capability import IdentityCapability
from shu.plugins.host.log_capability import LogCapability
from shu.plugins.host.utils_capability import UtilsCapability
from shu.plugins.sandbox.exceptions import serialize_exc
from shu.plugins.sandbox.logging_ferry import drain_loop, install_queue_handler
from shu.plugins.sandbox.proxy_host import ProxyHost
from shu.plugins.sandbox.rpc import (
    MSG_EXECUTE,
    ChildMessage,
    read_frame,
    write_frame,
)
from shu.plugins.sandbox.rpc_client import connect

# Deny list: these modules must never be importable by plugin code in
# the child.  The subprocess boundary means there is no trusted host
# code sharing the process — every import comes from the plugin, so
# no stack-walk trust check is needed (unlike the in-process
# _DenyImportsFinder in executor.py).
_DENIED_MODULES: frozenset[str] = frozenset({
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
})


def _is_denied(fullname: str) -> bool:
    """Return True if *fullname* matches any entry in the deny list.

    Matches both exact names (``"socket"``) and sub-packages
    (``"socket.something"``, ``"shu.core.config"``).
    """
    for denied in _DENIED_MODULES:
        if fullname == denied or fullname.startswith(denied + "."):
            return True
    return False


class _SandboxedFinder(MetaPathFinder):
    """Meta-path finder that raises ``ImportError`` for denied modules."""

    def find_module(
        self, fullname: str, path: object = None,
    ) -> None:
        # find_module is deprecated but some loaders still call it;
        # delegate to find_spec which does the actual check.
        return None

    def find_spec(
        self,
        fullname: str,
        path: object = None,
        target: ModuleType | None = None,
    ) -> ModuleSpec | None:
        """
        We pretend to be a legitimate finder to see what is requested to be imported.
        We never actually import anything, we just abort if it is something that we
        don't want imported.
        """
        if _is_denied(fullname):
            raise ImportError(
                f"Import of '{fullname}' is blocked in the plugin sandbox."
            )
        return None


def install_import_guard() -> None:
    """Block plugin access to denied modules via three complementary paths.

    1. Install :class:`_SandboxedFinder` on ``sys.meta_path`` so any new
       import of a denied name raises ``ImportError``.
    2. Scrub ``sys.modules`` of denied entries that were loaded
       transitively during bootstrap (e.g. ``urllib`` pulled in by some
       stdlib dependency). Without this, ``import urllib`` would return
       the cached copy without ever consulting the finder.
    3. Patch ``importlib.import_module`` and ``builtins.__import__`` to
       reject denied names before they reach the import system — defense
       in depth against future sys.modules repopulation.

    Must be called *before* the plugin module is imported so that any
    transitive imports from the plugin hit the deny list.
    """

    # _SandboxedFinder hooks into Python's import machinery. Specifically: Python's
    # import system has a list called sys.meta_path — an ordered chain of "finder"
    # objects. When you write import foo, Python walks that list and asks each finder
    # "do you know how to locate foo?" via find_spec(fullname). The first one that
    # returns a spec wins; finders that return None get skipped. install_import_guard()
    # inserts _SandboxedFinder at position 0 (child_bootstrap.py:116), so it gets
    # consulted before any real finder.
    sys.meta_path.insert(0, _SandboxedFinder())

    # Scrub any already-cached denied modules. The bootstrap's own
    # closures over these modules (e.g. serialize_exc, ProxyHost) keep
    # the module objects alive via references; we only remove the
    # sys.modules dict entry so future ``import`` lookups miss the cache.
    for name in list(sys.modules.keys()):
        if _is_denied(name):
            del sys.modules[name]

    # Patch importlib.import_module to reject by name. Dynamic-import
    # helpers in plugin code (e.g. ``importlib.import_module("urllib")``)
    # would otherwise re-import and succeed because stdlib-finder logic
    # kicks in before our meta_path finder is consulted for some paths.
    _orig_import_module = importlib.import_module

    def _guarded_import_module(name: str, package: str | None = None) -> ModuleType:
        if _is_denied(name):
            raise ImportError(
                f"Import of '{name}' is blocked in the plugin sandbox."
            )
        return _orig_import_module(name, package)

    importlib.import_module = _guarded_import_module  # type: ignore[assignment]

    # Patch builtins.__import__ for the same reason — covers the
    # ``import foo`` statement form which does not call
    # importlib.import_module directly.
    _orig_builtin_import = builtins.__import__

    def _guarded_builtin_import(
        name: str,
        globals: dict[str, Any] | None = None,
        locals: dict[str, Any] | None = None,
        fromlist: tuple[str, ...] = (),
        level: int = 0,
    ) -> ModuleType:
        if _is_denied(name):
            raise ImportError(
                f"Import of '{name}' is blocked in the plugin sandbox."
            )
        return _orig_builtin_import(name, globals, locals, fromlist, level)

    builtins.__import__ = _guarded_builtin_import  # type: ignore[assignment]


def _raising_open(*args: object, **kwargs: object) -> None:
    raise PermissionError("Sandbox blocks filesystem access")


def _raising_spawn(*args: object, **kwargs: object) -> None:
    raise PermissionError("Sandbox blocks process spawning")


def _raising_asyncio(*args: object, **kwargs: object) -> None:
    raise PermissionError("Sandbox blocks asyncio network/subprocess operations")


async def _async_raising_asyncio(*args: object, **kwargs: object) -> None:
    raise PermissionError("Sandbox blocks asyncio network/subprocess operations")


# NOTE: os.read / os.write and other existing-fd primitives are
# deliberately NOT stubbed, and must stay that way. The sandbox talks
# to the parent over an already-open UDS file descriptor (see connect()
# in rpc_client.py), and fds 0/1/2 are used by the logging ferry.
# Stubbing read/write would silently brick the sandbox's communication
# channel.
#
# The design only blocks *opening* new files: a plugin cannot go from
# a filesystem path to a readable object, because every path→fd and
# fd→file-object conversion below raises. Plugins have no way to
# obtain a handle on the UDS fd — it's held in a closure inside the
# bootstrap — so leaving os.read/os.write intact is safe.
def install_fs_guard() -> None:
    """Replace filesystem-access functions with raising stubs.

    Prevents plugin code from reading ``.env``, ``/proc/self/environ``,
    K8s secret mounts, or any other file — directly (``open``,
    ``os.open``), through a different stdlib surface (``io.open``,
    ``os.fdopen``), or via ``pathlib``.

    Must be called *after* all bootstrap-internal imports and file I/O
    are complete — once installed, the bootstrap itself can no longer
    open files either.
    """
    # Low-level entry points.
    os.open = _raising_open  # type: ignore[assignment]
    if hasattr(os, "openat"):
        os.openat = _raising_open  # type: ignore[assignment]
    # fdopen opens a Python file object from a pre-existing fd (0/1/2
    # or anything the bootstrap may have opened); stub so plugins can't
    # wrap an inherited fd into a readable file.
    os.fdopen = _raising_open  # type: ignore[assignment]

    # High-level entry points. ``io.open`` and ``builtins.open`` are the
    # same callable in CPython, but they are looked up via different
    # module attributes — stub both.
    builtins.open = _raising_open  # type: ignore[assignment]
    io.open = _raising_open  # type: ignore[assignment]

    # pathlib.Path.read_text / read_bytes go through Path.open, which
    # calls io.open — so stubbing io.open already covers them. Stub
    # Path.open directly as defense in depth against future pathlib
    # changes that bypass io.open.
    pathlib.Path.open = _raising_open  # type: ignore[assignment]


# Functions that must be stubbed to prevent process spawning.
# Always-available on POSIX; hasattr guards handle rare platform gaps.
_SPAWN_FUNCTIONS: list[str] = [
    "system", "popen",
    "execv", "execve", "execvp", "execvpe",
    "execl", "execle", "execlp", "execlpe",
    "fork", "forkpty",
    "spawnv", "spawnve", "spawnvp", "spawnvpe",
    "spawnl", "spawnle", "spawnlp", "spawnlpe",
    "posix_spawn", "posix_spawnp",
]


def install_spawn_guard() -> None:
    """Replace process-spawning functions on ``os`` with raising stubs.

    Prevents plugin code from shelling out or forking to read files,
    environment variables, or secret mounts indirectly.

    Must be called *after* all bootstrap-internal process operations
    are complete.
    """
    for name in _SPAWN_FUNCTIONS:
        if hasattr(os, name):
            setattr(os, name, _raising_spawn)


def install_loader_guard() -> None:
    """Block ``importlib.machinery`` file loaders from reading files.

    ``install_fs_guard`` patches the ``open`` family, but
    ``importlib.machinery.SourceFileLoader(path).get_data(path)`` reads
    files through low-level primitives that bypass those patches.  A
    plugin could do::

        SourceFileLoader("x", "/etc/passwd").get_data("/etc/passwd")

    to exfiltrate a file without ever calling ``open``.

    MUST be called *after* the plugin module has been imported — the
    import machinery itself calls ``get_data`` to read the plugin's
    ``.py`` file, so guarding too early breaks plugin loading. Once
    installed, late imports from inside ``plugin.execute()`` that hit
    ``get_data`` will also fail; the design expects plugin deps at
    module level.
    """
    for loader_cls in (
        importlib.machinery.SourceFileLoader,
        importlib.machinery.SourcelessFileLoader,
        importlib.machinery.ExtensionFileLoader,
    ):
        loader_cls.get_data = _raising_open  # type: ignore[assignment]


# asyncio attributes that open network connections, start servers, or
# spawn subprocesses — i.e. dangerous equivalents of socket / subprocess
# that live in a module we cannot put on the import deny list (asyncio
# itself is load-bearing for the sandbox).
_ASYNCIO_MODULE_FUNCTIONS: list[str] = [
    "open_connection",
    "open_unix_connection",
    "start_server",
    "start_unix_server",
    "create_subprocess_exec",
    "create_subprocess_shell",
]

# Event-loop methods that open connections or spawn subprocesses.
# Every concrete event-loop class (_UnixSelectorEventLoop,
# BaseProactorEventLoop, …) inherits these from BaseEventLoop, so
# patching once at the base class covers both the running loop and
# any loop a plugin could create via asyncio.new_event_loop().
# Module-level asyncio.open_connection etc. ultimately call these —
# but plugin code could also call the loop methods directly.
_ASYNCIO_LOOP_METHODS: list[str] = [
    "create_connection",
    "create_unix_connection",
    "create_server",
    "create_unix_server",
    "create_datagram_endpoint",
    "connect_accepted_socket",
    "connect_read_pipe",
    "connect_write_pipe",
    "sock_connect",
    "subprocess_exec",
    "subprocess_shell",
]


def install_asyncio_guard() -> None:
    """Replace asyncio network/subprocess helpers with raising stubs.

    ``asyncio`` itself must stay importable (the sandbox bootstrap uses
    it for the UDS), but its network and subprocess primitives are a
    bypass of the ``socket`` / ``subprocess`` deny list: a plugin could
    call ``asyncio.open_connection('...')`` without ever importing
    ``socket`` directly.

    Patches three surfaces:

    * Module-level helpers (``asyncio.open_connection``, etc.).
    * Loop methods on ``BaseEventLoop`` — at the class, not the running
      instance, so every subclass inherits the patch and a plugin cannot
      sidestep by obtaining a fresh loop.
    * ``asyncio.new_event_loop`` — the common factory. A plugin could
      still route around this via ``asyncio.get_event_loop_policy().new_event_loop()``,
      but any loop it obtains that way still has its dangerous methods
      patched at the class level, so the fresh loop is inert.

    Must be called from inside the running loop, after the UDS is
    already connected (the handshake runs before this guard). Stubbing
    ``new_event_loop`` would break ``asyncio.run()`` if applied earlier,
    and the running loop no longer needs to create connections or
    subprocesses of its own by this point.

    TODO(SHU-681): Known bypass — asyncio internals retain direct
    references to ``socket`` and ``subprocess``. Because asyncio is
    imported before lockdown, ``asyncio.base_events`` and
    ``asyncio.subprocess`` are cached in ``sys.modules`` with
    ``asyncio.base_events.socket`` already bound to the real
    ``socket`` module. A determined plugin can walk those attributes
    (``asyncio.base_events.socket.socket(...)``) without ever
    invoking an import that the deny list would see. Intentionally
    left unfixed at the Python level: SHU-681 adds seccomp-bpf, which
    blocks the dangerous syscalls at the kernel regardless of which
    Python references the plugin can reach. Python-level attribute
    scrubbing across ~14 retained references would be whack-a-mole
    that seccomp makes redundant.
    """
    for name in _ASYNCIO_MODULE_FUNCTIONS:
        if hasattr(asyncio, name):
            setattr(asyncio, name, _async_raising_asyncio)

    # Patch at the class level so every loop instance — current and
    # future — resolves these methods to the raising stub via MRO.
    base_event_loop_cls = asyncio.base_events.BaseEventLoop
    for name in _ASYNCIO_LOOP_METHODS:
        if hasattr(base_event_loop_cls, name):
            # Methods on EventLoop are sync or async depending on API;
            # _async_raising_asyncio works for both because the caller
            # always awaits the result (or wraps in a task).
            setattr(base_event_loop_cls, name, _async_raising_asyncio)

    # Block the common factory. A plugin could still reach
    # policy.new_event_loop() directly, but any loop it gets back has
    # the class-level patches above, so it's inert. Patching the policy
    # class here would also break pytest-asyncio's own teardown, which
    # uses it to reset the test's event loop.
    asyncio.new_event_loop = _raising_asyncio  # type: ignore[assignment]


async def main(uds_path: str) -> None:
    """Child bootstrap entry point.

    Execution order is load-bearing — each step depends on the prior:

    1.  Connect to parent UDS (before any lockdown).
    2.  Read handshake payload.
    3.  Construct local capabilities (identity, log, utils) from handshake.
    4.  Install import guard  — blocks ``shu.*``, network, FFI modules.
    5.  Install filesystem guard — stubs ``os.open``, ``builtins.open``,
        ``io.open``, ``os.fdopen``, ``pathlib.Path.open``.
    6.  Install spawn guard     — stubs ``os.system``, ``os.fork``, etc.
    7.  Install asyncio guard   — stubs asyncio network/subprocess
        helpers on the running loop (the ``socket``/``subprocess`` deny
        list doesn't help if ``asyncio.open_connection`` is reachable).
    8.  Install logging ferry   — QueueHandler + async drain task.
    9.  Import plugin module + resolve class (runs under full sandbox).
    9b. Install loader guard    — stubs ``FileLoader.get_data``; must
        happen *after* plugin import since the import machinery itself
        uses it.
    10. Instantiate plugin (``PluginClass()``).
    11. Send ``MSG_READY``.
    12. Await ``MSG_EXECUTE`` with vparams.
    13. Build ``ProxyHost``, run ``plugin.execute()``, send result.
    """
    # -- Step 1-2: UDS connection and handshake --
    client = await connect(uds_path)
    handshake = await client.read_handshake()

    # -- Step 3: Construct local capabilities from handshake values --
    identity = IdentityCapability(
        user_id=handshake["user_id"],
        user_email=handshake.get("user_email"),
        providers=handshake.get("providers"),
    )
    log_cap = LogCapability(
        plugin_name=handshake["plugin_module"],
        user_id=handshake["user_id"],
    )
    utils_cap = UtilsCapability(
        plugin_name=handshake["plugin_module"],
        user_id=handshake["user_id"],
    )

    # -- Step 4-7: Install sandbox guards --
    # Order: imports first (so plugin import is blocked), then fs/spawn,
    # then asyncio (must run after the UDS is connected — which it is).
    install_import_guard()
    install_fs_guard()
    install_spawn_guard()
    install_asyncio_guard()

    # -- Step 8: Install logging ferry --
    q, handler = install_queue_handler()
    drain_task = asyncio.create_task(drain_loop(q, handler, client._writer))

    try:
        # -- Step 9: Import plugin (still needs importlib.get_data access) --
        plugin_mod = importlib.import_module(handshake["plugin_module"])
        plugin_cls = getattr(plugin_mod, handshake["plugin_class"])

        # -- Step 9b: Lock down importlib loaders now that the plugin is
        # imported. Bypass: SourceFileLoader(path).get_data(path) reads
        # files without going through the patched open family.
        install_loader_guard()

        # -- Step 10: Instantiate plugin under full sandbox --
        plugin = plugin_cls()

        # -- Step 11: Signal readiness --
        await write_frame(client._writer, ChildMessage.ready())

        # -- Step 12: Await execute command --
        exec_frame = await read_frame(client._reader)
        if exec_frame.get("type") != MSG_EXECUTE:
            raise RuntimeError(f"Expected execute, got {exec_frame.get('type')!r}")
        vparams: dict[str, Any] = exec_frame["vparams"]

        # -- Step 13: Build ProxyHost, run plugin, send result --
        await client.start_reader()
        host = ProxyHost(
            client=client,
            declared_caps=set(handshake.get("capabilities", [])),
            identity=identity,
            log=log_cap,
            utils=utils_cap,
        )
        ctx = ExecuteContext(user_id=handshake["user_id"])

        result: PluginResult = await plugin.execute(vparams, ctx, host)
        await write_frame(client._writer, ChildMessage.final_result(result.model_dump()))

    except Exception as exc:
        tb_text = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
        exc_payload = serialize_exc(exc)
        await write_frame(
            client._writer,
            ChildMessage.final_error(
                exc_type=exc_payload["exc_type"],
                payload=exc_payload.get("payload", {}),
                traceback_text=tb_text,
            ),
        )
    finally:
        drain_task.cancel()
        try:
            await drain_task
        except asyncio.CancelledError:
            pass
        await client.close()


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1]))
