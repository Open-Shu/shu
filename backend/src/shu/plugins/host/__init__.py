"""Host-side plugin capabilities.

Intentionally empty of re-exports. Eager imports here would pull
``host_builder`` into every touch of this package, which cascades through
``kb_capability → services → llm → plugin_execution → executor → host_builder``
and creates a circular import. The sandbox child bootstrap also relies on
leaf submodules being loadable without dragging in the full host graph.

Import submodules directly: ``from shu.plugins.host.exceptions import ...``,
``from shu.plugins.host.host_builder import make_host``, etc.
"""
