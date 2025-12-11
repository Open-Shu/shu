PLUGIN_MANIFEST = {
    "name": "test_hostcaps",
    "version": "1",
    "module": "plugins.test_hostcaps.plugin:TestHostcapsPlugin",
    # Capability whitelist for exercising host surfaces
    "capabilities": ["secrets", "storage", "cache", "identity"],
}

