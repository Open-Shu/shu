"""Parent-side orchestrator for per-invocation plugin sandbox subprocesses.

Spawns a fresh Python subprocess for each plugin.execute(), scrubs its
environment to only essential variables, wires up a UDS control channel,
and enforces a global wall-clock timeout (SIGTERM then SIGKILL).

Returns a :class:`PluginResult`: the plugin's own result on success, or
``PluginResult.err(code="plugin_sandbox_*")`` on sandbox-level failure.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import signal
import sys
import tempfile
from typing import TYPE_CHECKING, Any

from shu.core.logging import get_logger
from shu.plugins.base import ExecuteContext, PluginResult
from shu.plugins.host.exceptions import (
    CapabilityDenied,
    EgressDenied,
    HttpRequestFailed,
)
from shu.plugins.sandbox.rpc_server import RpcServer

if TYPE_CHECKING:
    from shu.core.config import Settings
    from shu.plugins.host.host_builder import Host

logger = get_logger(__name__)

_KILL_GRACE_SECONDS = 2
_STDERR_TAIL_BYTES = 2000


class SandboxLauncher:
    """Parent-side orchestrator for per-invocation plugin sandbox subprocesses.

    Spawns a fresh Python subprocess for each plugin.execute(), scrubs
    its environment to only PATH/HOME/LANG, wires up a UDS control
    channel, and enforces a global wall-clock timeout (SIGTERM followed
    by a grace period then SIGKILL).

    Returns a PluginResult: the plugin's own result on success, or
    PluginResult.err(code="plugin_sandbox_*") on sandbox-level failure.
    """

    def __init__(self, timeout_seconds: int, settings: Settings) -> None:
        self._timeout_seconds = timeout_seconds
        self._settings = settings

    async def run(
        self,
        *,
        plugin_module: str,
        plugin_class: str,
        vparams: dict[str, Any],
        exec_ctx: ExecuteContext,
        host: Host,
        user_id: str,
        user_email: str | None = None,
        provider_identities: dict[str, Any] | None = None,
        execution_id: str | None = None,
    ) -> PluginResult:
        """Spawn a sandboxed subprocess, run the plugin, and return its result."""
        sock_dir, uds_path = self._create_uds_dir()
        uds_server: asyncio.AbstractServer | None = None
        process: asyncio.subprocess.Process | None = None

        # One wall-clock budget for connect + RPC combined. Previously each
        # phase had its own timeout, so a slow child that ate most of the
        # connect budget could still get a full budget for the RPC phase —
        # doubling the worst-case wall time and violating the documented
        # contract that ``timeout_seconds`` is the end-to-end cap.
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self._timeout_seconds

        try:
            connection_ready: asyncio.Future[
                tuple[asyncio.StreamReader, asyncio.StreamWriter]
            ] = loop.create_future()

            def on_connect(
                reader: asyncio.StreamReader, writer: asyncio.StreamWriter
            ) -> None:
                if not connection_ready.done():
                    connection_ready.set_result((reader, writer))

            uds_server = await asyncio.start_unix_server(on_connect, path=uds_path)
            # Narrow attack surface: only the current UID can connect.
            # Defense in depth on top of the 0700 directory perms from mkdtemp.
            os.chmod(uds_path, 0o600)

            process = await self._spawn_child(uds_path)

            reader, writer = await self._await_connection(
                connection_ready, process, plugin_module,
                timeout=max(deadline - loop.time(), 0.0),
            )

            return await self._run_rpc(
                uds_path=uds_path,
                reader=reader,
                writer=writer,
                vparams=vparams,
                host=host,
                user_id=user_id,
                user_email=user_email,
                provider_identities=provider_identities,
                plugin_module=plugin_module,
                plugin_class=plugin_class,
                execution_id=execution_id,
                exec_ctx=exec_ctx,
                timeout=max(deadline - loop.time(), 0.0),
            )

        except asyncio.TimeoutError:
            stderr_tail = await self._terminate_child(process)
            logger.warning(
                "plugin sandbox timed out",
                extra={
                    "plugin_module": plugin_module,
                    "timeout_seconds": self._timeout_seconds,
                    "stderr_tail": stderr_tail,
                },
            )
            return PluginResult.err(
                code="plugin_sandbox_timeout",
                message=(
                    f"Plugin exceeded timeout of {self._timeout_seconds}s"
                ),
                details={"stderr_tail": stderr_tail},
            )

        except _ChildCrashedBeforeConnect as exc:
            logger.warning(
                "child crashed before UDS connect",
                extra={
                    "plugin_module": plugin_module,
                    "returncode": exc.returncode,
                    "stderr_tail": exc.stderr_tail,
                },
            )
            return PluginResult.err(
                code="plugin_sandbox_child_crashed",
                message=(
                    f"Sandbox child exited with code {exc.returncode} "
                    f"before connecting"
                ),
                details={
                    "returncode": exc.returncode,
                    "stderr_tail": exc.stderr_tail,
                },
            )

        except ConnectionError as exc:
            stderr_tail = await self._terminate_child(process)
            logger.warning(
                "sandbox transport error",
                extra={
                    "plugin_module": plugin_module,
                    "error": str(exc),
                    "stderr_tail": stderr_tail,
                },
            )
            return PluginResult.err(
                code="plugin_sandbox_transport_error",
                message=f"Sandbox transport error: {exc}",
                details={"stderr_tail": stderr_tail},
            )

        # Business exceptions from the plugin (raised inside the child,
        # serialized over RPC, re-raised here by rpc_server.serve_on) must
        # propagate to the executor, which has dedicated handling — e.g.
        # HttpRequestFailed → provider_error PluginResult with retry_after,
        # error_category, etc. Swallowing them into plugin_sandbox_plugin_error
        # strips that structured context.
        except (HttpRequestFailed, CapabilityDenied, EgressDenied):
            await self._terminate_child(process)
            raise

        except Exception as exc:
            stderr_tail = await self._terminate_child(process)
            logger.error(
                "sandbox plugin error",
                extra={
                    "plugin_module": plugin_module,
                    "error": str(exc),
                    "error_type": type(exc).__name__,
                    "stderr_tail": stderr_tail,
                },
            )
            return PluginResult.err(
                code="plugin_sandbox_plugin_error",
                message=str(exc),
                details={
                    "error_type": type(exc).__name__,
                    "stderr_tail": stderr_tail,
                },
            )

        finally:
            await self._cleanup(uds_server, process, sock_dir)

    def _create_uds_dir(self) -> tuple[str, str]:
        """Create a temporary directory with a short socket path inside.

        Uses mkdtemp (0700 by default) so only the current UID can see
        the directory contents, even before we chmod the socket itself.
        The socket is named ``sock`` to keep the full path well under
        macOS's ~104-byte sun_path limit.
        """
        sock_dir = tempfile.mkdtemp(prefix="shu-sandbox-")
        uds_path = os.path.join(sock_dir, "sock")
        return sock_dir, uds_path

    def _build_handshake_payload(
        self,
        *,
        host: Host,
        user_id: str,
        user_email: str | None,
        provider_identities: dict[str, Any] | None,
        plugin_module: str,
        plugin_class: str,
        execution_id: str | None,
        exec_ctx: ExecuteContext,
    ) -> dict[str, Any]:
        """Assemble the handshake dict the child expects.

        Reads declared_caps from the host directly rather than
        recomputing auto-add rules. Sorted for deterministic output
        in logs and tests.
        """
        # host._declared_caps is the post-auto-add set (e.g. "kb" -> also "cursor").
        # We read it instead of duplicating make_host's auto-add logic.
        effective_caps: set[str] = object.__getattribute__(host, "_declared_caps")

        return {
            "user_id": user_id,
            "user_email": user_email,
            "providers": provider_identities or {},
            "plugin_module": plugin_module,
            "plugin_class": plugin_class,
            "capabilities": sorted(effective_caps),
            "execution_id": execution_id,
            # Ship agent_key so the child can reconstruct ExecuteContext
            # faithfully — plugins read ctx.agent_key to scope per-agent
            # state, and losing it would silently make every invocation
            # look like it has no agent identity.
            "agent_key": exec_ctx.agent_key,
        }

    def _scrubbed_env(self) -> dict[str, str]:
        """Build a minimal environment for the child subprocess.

        Only PATH, HOME, and LANG are forwarded from the parent.
        PYTHONPATH is derived from ``sys.path`` rather than
        ``os.environ["PYTHONPATH"]`` because ``load_dotenv(override=True)``
        in ``shu.core.config`` clobbers os.environ at import time with
        the literal contents of ``.env``, which may contain unexpanded
        shell syntax (e.g. ``$(pwd)/src``). ``sys.path`` was populated
        correctly at Python startup before dotenv ran, so it's the
        reliable source for the child's import search path.
        The import guard still blocks plugin-code imports of ``shu.*``.
        """
        env: dict[str, str] = {}
        for key in ("PATH", "HOME", "LANG"):
            val = os.environ.get(key)
            if val is not None:
                env[key] = val
        env["PYTHONPATH"] = os.pathsep.join(sys.path)
        return env

    async def _spawn_child(self, uds_path: str) -> asyncio.subprocess.Process:
        """Launch the sandbox child subprocess."""
        return await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "shu.plugins.sandbox.child_bootstrap",
            uds_path,
            env=self._scrubbed_env(),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

    async def _await_connection(
        self,
        connection_ready: asyncio.Future[
            tuple[asyncio.StreamReader, asyncio.StreamWriter]
        ],
        process: asyncio.subprocess.Process,
        plugin_module: str,
        timeout: float,
    ) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
        """Wait for the child to connect to the UDS, or detect early death.

        Uses asyncio.wait with FIRST_COMPLETED: if the child exits
        before connecting, we collect stderr and raise
        _ChildCrashedBeforeConnect. *timeout* is the remaining share
        of the run()-level deadline, not a per-phase budget.
        """
        child_exit_task = asyncio.create_task(process.wait())
        connect_task = asyncio.ensure_future(connection_ready)

        done, pending = await asyncio.wait(
            {child_exit_task, connect_task},
            timeout=timeout,
            return_when=asyncio.FIRST_COMPLETED,
        )

        if not done:
            # Both timed out — neither connected nor exited.
            child_exit_task.cancel()
            connect_task.cancel()
            raise asyncio.TimeoutError()

        if child_exit_task in done:
            connect_task.cancel()
            returncode = child_exit_task.result()
            stderr_tail = await self._read_stderr_tail(process)
            raise _ChildCrashedBeforeConnect(
                returncode=returncode,
                stderr_tail=stderr_tail,
            )

        # Child connected. Cancel the exit-watcher — we'll handle
        # child death via the RPC loop from here.
        child_exit_task.cancel()
        return connect_task.result()

    async def _run_rpc(
        self,
        *,
        uds_path: str,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        vparams: dict[str, Any],
        host: Host,
        user_id: str,
        user_email: str | None,
        provider_identities: dict[str, Any] | None,
        plugin_module: str,
        plugin_class: str,
        execution_id: str | None,
        exec_ctx: ExecuteContext,
        timeout: float,
    ) -> PluginResult:
        """Drive the RPC handshake/execute/result cycle under the timeout.

        Protocol: write MSG_HANDSHAKE first, then MSG_EXECUTE. The child
        reads handshake, performs sandbox lockdown, sends MSG_READY,
        then reads MSG_EXECUTE. Parent does not need to observe MSG_READY
        because read_frame is blocking on the child side — MSG_EXECUTE
        is consumed only after the child is ready.
        """
        handshake = self._build_handshake_payload(
            host=host,
            user_id=user_id,
            user_email=user_email,
            provider_identities=provider_identities,
            plugin_module=plugin_module,
            plugin_class=plugin_class,
            execution_id=execution_id,
            exec_ctx=exec_ctx,
        )
        server = RpcServer(host, handshake)

        # Sequence: handshake first, then execute, then drive the read
        # loop. serve_on is responsible for the read loop only; the
        # launcher owns the outbound write ordering so we can sequence
        # MSG_HANDSHAKE → MSG_EXECUTE deterministically on the wire.
        await server.send_handshake(writer)
        await server.send_execute(writer, vparams)

        result_dict = await asyncio.wait_for(
            server.serve_on(uds_path, reader, writer),
            timeout=timeout,
        )
        return PluginResult.model_validate(result_dict)

    async def _terminate_child(
        self,
        process: asyncio.subprocess.Process | None,
    ) -> str:
        """SIGTERM the child, wait briefly, then SIGKILL if still alive.

        Returns the last bytes of stderr for diagnostics.
        """
        if process is None or process.returncode is not None:
            return await self._read_stderr_tail(process) if process else ""

        try:
            process.send_signal(signal.SIGTERM)
        except ProcessLookupError:
            return await self._read_stderr_tail(process)

        try:
            await asyncio.wait_for(
                process.wait(), timeout=_KILL_GRACE_SECONDS
            )
        except asyncio.TimeoutError:
            try:
                process.kill()
            except ProcessLookupError:
                pass
            # Wait unconditionally so the process is fully reaped
            await process.wait()

        return await self._read_stderr_tail(process)

    async def _read_stderr_tail(
        self, process: asyncio.subprocess.Process | None
    ) -> str:
        """Return the last N bytes of the child's stderr.

        Bounded to _STDERR_TAIL_BYTES (2000) so a runaway child
        cannot balloon the response while still capturing a full
        Python traceback in most cases.
        """
        if process is None or process.stderr is None:
            return ""
        try:
            raw = await process.stderr.read()
            return raw[-_STDERR_TAIL_BYTES:].decode("utf-8", errors="replace")
        except Exception:
            return ""

    async def _cleanup(
        self,
        uds_server: asyncio.AbstractServer | None,
        process: asyncio.subprocess.Process | None,
        sock_dir: str,
    ) -> None:
        """Ensure the UDS server, child process, and temp dir are cleaned up."""
        if uds_server is not None:
            uds_server.close()
            await uds_server.wait_closed()

        if process is not None and process.returncode is None:
            await self._terminate_child(process)

        shutil.rmtree(sock_dir, ignore_errors=True)


class _ChildCrashedBeforeConnect(Exception):
    """Internal signal: the child exited before connecting to the UDS."""

    def __init__(self, returncode: int | None, stderr_tail: str) -> None:
        super().__init__(f"Child exited with code {returncode}")
        self.returncode = returncode
        self.stderr_tail = stderr_tail
