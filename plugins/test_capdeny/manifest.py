from __future__ import annotations

PLUGIN_MANIFEST = {
    "name": "test_capdeny",
    "version": "1",
    "module": "plugins.test_capdeny.plugin:TestCapDenyPlugin",
    "capabilities": ["identity"],  # deliberately omit 'secrets'
}

