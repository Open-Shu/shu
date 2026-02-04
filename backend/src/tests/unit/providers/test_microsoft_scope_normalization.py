"""
Unit tests for Microsoft scope normalization.

Verifies that Microsoft scopes are normalized correctly:
- Graph API resource scopes (Mail.Read, Calendars.Read, etc.) get the full URL prefix
- OIDC protocol scopes (openid, profile, email, offline_access) remain unprefixed
"""

from shu.api.host_auth import normalize_microsoft_scopes


def test_scope_normalization_graph_scopes_prefixed():
    """Test that Graph API resource scopes get the URL prefix."""
    token_scopes = ["Mail.Read", "Calendars.Read", "User.Read"]

    normalized_scopes = normalize_microsoft_scopes(token_scopes)

    assert "https://graph.microsoft.com/Mail.Read" in normalized_scopes
    assert "https://graph.microsoft.com/Calendars.Read" in normalized_scopes
    assert "https://graph.microsoft.com/User.Read" in normalized_scopes


def test_scope_normalization_oidc_scopes_unprefixed():
    """Test that OIDC protocol scopes remain unprefixed."""
    token_scopes = ["openid", "profile", "email", "offline_access"]

    normalized_scopes = normalize_microsoft_scopes(token_scopes)

    # OIDC scopes should remain as-is, not prefixed
    assert "openid" in normalized_scopes
    assert "profile" in normalized_scopes
    assert "email" in normalized_scopes
    assert "offline_access" in normalized_scopes

    # Should NOT have prefixed versions
    assert "https://graph.microsoft.com/openid" not in normalized_scopes
    assert "https://graph.microsoft.com/profile" not in normalized_scopes
    assert "https://graph.microsoft.com/email" not in normalized_scopes
    assert "https://graph.microsoft.com/offline_access" not in normalized_scopes


def test_scope_normalization_mixed_scopes():
    """Test normalization with mixed Graph API and OIDC scopes."""
    token_scopes = ["Mail.Read", "offline_access", "openid", "Calendars.Read"]

    normalized_scopes = normalize_microsoft_scopes(token_scopes)

    # Graph scopes should be prefixed
    assert "https://graph.microsoft.com/Mail.Read" in normalized_scopes
    assert "https://graph.microsoft.com/Calendars.Read" in normalized_scopes

    # OIDC scopes should remain unprefixed
    assert "offline_access" in normalized_scopes
    assert "openid" in normalized_scopes
    assert "https://graph.microsoft.com/offline_access" not in normalized_scopes
    assert "https://graph.microsoft.com/openid" not in normalized_scopes


def test_scope_normalization_already_normalized():
    """Test that already-normalized scopes are not double-prefixed."""
    token_scopes = ["https://graph.microsoft.com/Mail.Read", "offline_access"]

    normalized_scopes = normalize_microsoft_scopes(token_scopes)

    # Already-prefixed scope should not be double-prefixed
    assert "https://graph.microsoft.com/Mail.Read" in normalized_scopes
    assert "https://graph.microsoft.com/https://graph.microsoft.com/Mail.Read" not in normalized_scopes

    # OIDC scope should remain unprefixed
    assert "offline_access" in normalized_scopes


def test_scope_normalization_empty_list():
    """Test normalization with empty scope list."""
    token_scopes = []

    normalized_scopes = normalize_microsoft_scopes(token_scopes)

    assert normalized_scopes == []


def test_scope_normalization_filters_none_and_empty():
    """Test that None and empty string scopes are filtered out."""
    token_scopes = ["Mail.Read", "", None, "openid"]

    normalized_scopes = normalize_microsoft_scopes(token_scopes)

    # Mail.Read should be prefixed
    assert "https://graph.microsoft.com/Mail.Read" in normalized_scopes
    # openid should remain unprefixed
    assert "openid" in normalized_scopes
    # Empty string and None should be filtered out
    assert "" not in normalized_scopes
    assert None not in normalized_scopes
    # Should only have 2 valid scopes
    assert len(normalized_scopes) == 2


def test_scope_normalization_realistic_microsoft_response():
    """Test with a realistic set of scopes returned by Microsoft."""
    # Microsoft typically returns these scopes after OAuth consent
    token_scopes = [
        "Mail.Read",
        "Mail.Send",
        "Calendars.Read",
        "User.Read",
        "openid",
        "profile",
        "email",
        "offline_access"
    ]

    normalized_scopes = normalize_microsoft_scopes(token_scopes)

    # Graph API scopes should be prefixed
    assert "https://graph.microsoft.com/Mail.Read" in normalized_scopes
    assert "https://graph.microsoft.com/Mail.Send" in normalized_scopes
    assert "https://graph.microsoft.com/Calendars.Read" in normalized_scopes
    assert "https://graph.microsoft.com/User.Read" in normalized_scopes

    # OIDC scopes should remain unprefixed
    assert "openid" in normalized_scopes
    assert "profile" in normalized_scopes
    assert "email" in normalized_scopes
    assert "offline_access" in normalized_scopes

    # Verify count - should have same number of scopes
    assert len(normalized_scopes) == len(token_scopes)
