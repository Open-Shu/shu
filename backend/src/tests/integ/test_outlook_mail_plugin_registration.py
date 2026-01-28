"""
Integration tests for Outlook Mail plugin registration and discovery.

Verifies that the plugin is properly registered and discoverable by the plugin system.
"""
from __future__ import annotations

import logging

from integ.base_integration_test import BaseIntegrationTestSuite, create_test_runner_script

logger = logging.getLogger(__name__)


async def test_outlook_mail_plugin_discoverable(client, db, auth_headers):
    """Verify Outlook Mail plugin is discoverable by plugin loader."""
    from shu.plugins.loader import PluginLoader

    loader = PluginLoader()
    records = loader.discover()
    
    # Verify plugin is discovered
    assert "outlook_mail" in records, f"outlook_mail plugin not discovered; found={list(records.keys())}"
    
    rec = records["outlook_mail"]
    
    # Verify basic manifest fields
    assert rec.name == "outlook_mail", f"Expected name 'outlook_mail', got '{rec.name}'"
    assert rec.display_name == "Outlook Mail", f"Expected display_name 'Outlook Mail', got '{rec.display_name}'"
    assert rec.version == "1", f"Expected version '1', got '{rec.version}'"
    assert rec.entry == "plugins.shu_outlook_mail.plugin:OutlookMailPlugin", \
        f"Expected entry 'plugins.shu_outlook_mail.plugin:OutlookMailPlugin', got '{rec.entry}'"
    
    # Verify capabilities
    expected_capabilities = ["http", "identity", "auth", "secrets", "kb", "cursor"]
    assert set(rec.capabilities) == set(expected_capabilities), \
        f"Expected capabilities {expected_capabilities}, got {rec.capabilities}"
    
    # Verify feed configuration
    assert rec.default_feed_op == "ingest", f"Expected default_feed_op 'ingest', got '{rec.default_feed_op}'"
    assert rec.allowed_feed_ops == ["ingest"], f"Expected allowed_feed_ops ['ingest'], got {rec.allowed_feed_ops}"
    assert set(rec.chat_callable_ops) == {"list", "digest"}, \
        f"Expected chat_callable_ops ['list', 'digest'], got {rec.chat_callable_ops}"
    
    # Verify OAuth requirements
    assert rec.required_identities is not None, "required_identities should not be None"
    assert len(rec.required_identities) == 1, f"Expected 1 required identity, got {len(rec.required_identities)}"
    
    identity_req = rec.required_identities[0]
    assert identity_req.get("provider") == "microsoft", \
        f"Expected provider 'microsoft', got '{identity_req.get('provider')}'"
    assert identity_req.get("mode") == "user", \
        f"Expected mode 'user', got '{identity_req.get('mode')}'"
    assert "https://graph.microsoft.com/Mail.Read" in identity_req.get("scopes", []), \
        f"Expected Mail.Read scope in {identity_req.get('scopes')}"
    
    # Verify op_auth is present
    assert rec.op_auth is not None, "op_auth should not be None"
    assert "list" in rec.op_auth, "op_auth should contain 'list'"
    assert "digest" in rec.op_auth, "op_auth should contain 'digest'"
    assert "ingest" in rec.op_auth, "op_auth should contain 'ingest'"
    
    logger.info("✓ Outlook Mail plugin discovered with correct manifest")


async def test_outlook_mail_manifest_loaded_correctly(client, db, auth_headers):
    """Verify Outlook Mail plugin manifest is correctly loaded."""
    from shu.plugins.loader import PluginLoader

    loader = PluginLoader()
    records = loader.discover()
    
    assert "outlook_mail" in records, "outlook_mail plugin not discovered"
    rec = records["outlook_mail"]
    
    # Verify no static violations (no disallowed imports)
    assert not rec.violations, f"Plugin has static violations: {rec.violations}"
    
    # Verify plugin can be loaded
    try:
        plugin = loader.load(rec)
        assert plugin is not None, "Plugin load returned None"
        assert hasattr(plugin, "name"), "Plugin missing 'name' attribute"
        assert plugin.name == "outlook_mail", f"Expected plugin.name 'outlook_mail', got '{plugin.name}'"
        logger.info("✓ Outlook Mail plugin loaded successfully")
    except Exception as e:
        raise AssertionError(f"Failed to load outlook_mail plugin: {e}")
    
    # Verify plugin has required methods
    assert hasattr(plugin, "get_schema"), "Plugin missing get_schema method"
    assert hasattr(plugin, "get_output_schema"), "Plugin missing get_output_schema method"
    assert hasattr(plugin, "execute"), "Plugin missing execute method"
    
    # Verify schema is valid
    schema = plugin.get_schema()
    assert schema is not None, "get_schema returned None"
    assert "properties" in schema, "Schema missing 'properties'"
    assert "op" in schema["properties"], "Schema missing 'op' property"
    
    # Verify op enum contains required operations
    op_def = schema["properties"]["op"]
    assert "enum" in op_def, "op property missing 'enum'"
    assert set(op_def["enum"]) == {"list", "digest", "ingest"}, \
        f"Expected op enum ['list', 'digest', 'ingest'], got {op_def['enum']}"
    
    # Verify no auth fields in schema (capability-driven auth)
    assert "auth_mode" not in schema["properties"], "Schema should not contain 'auth_mode'"
    assert "user_email" not in schema["properties"], "Schema should not contain 'user_email'"
    assert "impersonate_email" not in schema["properties"], "Schema should not contain 'impersonate_email'"
    
    logger.info("✓ Outlook Mail plugin manifest loaded correctly")


async def test_outlook_mail_appears_in_connected_accounts(client, db, auth_headers):
    """Verify Outlook Mail plugin appears in Connected Accounts UI."""
    # Query the plugins API endpoint (used by Connected Accounts UI)
    response = await client.get("/api/v1/plugins", headers=auth_headers)
    assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
    
    data = response.json()
    assert "data" in data, "Response missing 'data' envelope"
    plugins_data = data["data"]
    
    # Find outlook_mail plugin in the list
    outlook_plugin = None
    for plugin in plugins_data:
        if plugin.get("name") == "outlook_mail":
            outlook_plugin = plugin
            break
    
    assert outlook_plugin is not None, \
        f"outlook_mail plugin not found in /api/v1/plugins response. Available: {[p.get('name') for p in plugins_data]}"
    
    # Verify plugin metadata
    assert outlook_plugin.get("display_name") == "Outlook Mail", \
        f"Expected display_name 'Outlook Mail', got '{outlook_plugin.get('display_name')}'"
    
    # Verify required_identities for Connected Accounts grouping
    required_identities = outlook_plugin.get("required_identities", [])
    assert len(required_identities) > 0, "Plugin should have required_identities"
    
    # Check that it's associated with microsoft provider
    has_microsoft = any(
        identity.get("provider") == "microsoft" 
        for identity in required_identities
    )
    assert has_microsoft, \
        f"Plugin should have microsoft provider in required_identities, got {required_identities}"
    
    # Verify it has the Mail.Read scope
    microsoft_identity = next(
        (identity for identity in required_identities if identity.get("provider") == "microsoft"),
        None
    )
    assert microsoft_identity is not None, "Should have microsoft identity"
    scopes = microsoft_identity.get("scopes", [])
    assert "https://graph.microsoft.com/Mail.Read" in scopes, \
        f"Expected Mail.Read scope in {scopes}"
    
    logger.info("✓ Outlook Mail plugin appears in Connected Accounts UI with correct metadata")


async def test_outlook_mail_plugin_registry_sync(client, db, auth_headers):
    """Verify Outlook Mail plugin can be synced to plugin registry."""
    from shu.plugins.registry import REGISTRY
    
    # Refresh registry to discover plugins
    REGISTRY.refresh()
    manifest = REGISTRY.get_manifest(refresh_if_empty=True)
    
    assert "outlook_mail" in manifest, \
        f"outlook_mail not in registry manifest. Available: {list(manifest.keys())}"
    
    # Sync to database
    sync_result = await REGISTRY.sync(db)
    
    # Verify sync result
    assert isinstance(sync_result, dict), "Sync should return a dict"
    logger.info(f"Registry sync result: {sync_result}")
    
    # Verify plugin is in database
    from sqlalchemy import select
    from shu.models.plugin_registry import PluginDefinition
    
    result = await db.execute(
        select(PluginDefinition).where(PluginDefinition.name == "outlook_mail")
    )
    plugin_def = result.scalars().first()
    
    assert plugin_def is not None, "outlook_mail plugin not found in database after sync"
    assert plugin_def.name == "outlook_mail", f"Expected name 'outlook_mail', got '{plugin_def.name}'"
    
    # Verify plugin has schemas
    assert plugin_def.input_schema is not None, "Plugin should have input_schema"
    assert plugin_def.output_schema is not None, "Plugin should have output_schema"
    
    # Note: Plugin is created with enabled=False by default (admin must enable)
    logger.info(f"✓ Outlook Mail plugin synced to registry (enabled={plugin_def.enabled})")


class OutlookMailPluginRegistrationSuite(BaseIntegrationTestSuite):
    """Test suite for Outlook Mail plugin registration and discovery."""
    
    def get_test_functions(self):
        return [
            test_outlook_mail_plugin_discoverable,
            test_outlook_mail_manifest_loaded_correctly,
            test_outlook_mail_appears_in_connected_accounts,
            test_outlook_mail_plugin_registry_sync,
        ]

    def get_suite_name(self) -> str:
        return "Outlook Mail Plugin Registration"

    def get_suite_description(self) -> str:
        return "Validates that Outlook Mail plugin is properly registered and discoverable"


if __name__ == "__main__":
    create_test_runner_script(OutlookMailPluginRegistrationSuite, globals())
