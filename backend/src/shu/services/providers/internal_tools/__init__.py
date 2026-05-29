"""Framework-internal tools — see SHU-816.

Internal tools are function-call-style capabilities Shu executes
in-process (rather than dispatching through the plugin system).
Routing uses an `int:` prefix on tool names; that prefix is owned
exclusively by ``InternalToolRouter`` and never appears on individual
tool definitions.
"""
