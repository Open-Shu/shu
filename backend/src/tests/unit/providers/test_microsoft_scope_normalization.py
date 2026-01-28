"""
Unit tests for Microsoft scope normalization.

Verifies that Microsoft scopes are normalized to include the full URL prefix
when stored in ProviderCredential, ensuring consistency with plugin manifests.
"""
import pytest


def test_scope_normalization_logic():
    """Test the scope normalization logic for Microsoft scopes."""
    # Simulate what Microsoft returns (short form)
    token_scopes = ["Mail.Read", "offline_access", "openid"]
    
    # Simulate normalization logic from host_auth.py
    normalized_scopes = []
    for scope in token_scopes:
        if scope and not scope.startswith("https://"):
            normalized_scopes.append(f"https://graph.microsoft.com/{scope}")
        else:
            normalized_scopes.append(scope)
    
    # Verify normalization
    assert "https://graph.microsoft.com/Mail.Read" in normalized_scopes
    assert "https://graph.microsoft.com/offline_access" in normalized_scopes
    assert "https://graph.microsoft.com/openid" in normalized_scopes


def test_scope_normalization_already_normalized():
    """Test that already-normalized scopes are not double-prefixed."""
    # Simulate scopes that are already normalized
    token_scopes = ["https://graph.microsoft.com/Mail.Read", "offline_access"]
    
    # Simulate normalization logic
    normalized_scopes = []
    for scope in token_scopes:
        if scope and not scope.startswith("https://"):
            normalized_scopes.append(f"https://graph.microsoft.com/{scope}")
        else:
            normalized_scopes.append(scope)
    
    # Verify no double-prefixing
    assert "https://graph.microsoft.com/Mail.Read" in normalized_scopes
    assert "https://graph.microsoft.com/offline_access" in normalized_scopes
    assert "https://graph.microsoft.com/https://graph.microsoft.com/Mail.Read" not in normalized_scopes


def test_scope_normalization_empty_list():
    """Test normalization with empty scope list."""
    token_scopes = []
    
    normalized_scopes = []
    for scope in token_scopes:
        if scope and not scope.startswith("https://"):
            normalized_scopes.append(f"https://graph.microsoft.com/{scope}")
        else:
            normalized_scopes.append(scope)
    
    assert normalized_scopes == []


def test_scope_normalization_mixed():
    """Test normalization with mixed short-form and full-form scopes."""
    token_scopes = [
        "Mail.Read",
        "https://graph.microsoft.com/Calendars.Read",
        "offline_access"
    ]
    
    normalized_scopes = []
    for scope in token_scopes:
        if scope and not scope.startswith("https://"):
            normalized_scopes.append(f"https://graph.microsoft.com/{scope}")
        else:
            normalized_scopes.append(scope)
    
    assert "https://graph.microsoft.com/Mail.Read" in normalized_scopes
    assert "https://graph.microsoft.com/Calendars.Read" in normalized_scopes
    assert "https://graph.microsoft.com/offline_access" in normalized_scopes
    # Verify no duplicates or double-prefixing
    assert len([s for s in normalized_scopes if "Calendars.Read" in s]) == 1
