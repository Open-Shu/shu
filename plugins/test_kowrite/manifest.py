PLUGIN_MANIFEST = {
    "name": "test_kowrite",
    "version": "1",
    "module": "plugins.test_kowrite.plugin:TestKoWritePlugin",
    # Requires kb to write KOs; identity used to set source.account in KO
    "capabilities": ["kb", "identity"],
}

