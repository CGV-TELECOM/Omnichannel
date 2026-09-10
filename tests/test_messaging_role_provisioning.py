"""Unit tests: OmniHub role → Chatwoot account role mapping."""

from app.services.v1.handle_chatwoot.user_tokens import (
    normalize_omnihub_role_name,
    omnihub_role_is_admin_partner,
    resolve_chatwoot_account_role,
)


def test_normalize_omnihub_role_name():
    assert normalize_omnihub_role_name("Admin-Partner") == "admin-partner"
    assert normalize_omnihub_role_name("admin_partner") == "admin-partner"
    assert normalize_omnihub_role_name("admin partner") == "admin-partner"
    assert normalize_omnihub_role_name(None) == ""


def test_omnihub_role_is_admin_partner():
    assert omnihub_role_is_admin_partner("admin-partner") is True
    assert omnihub_role_is_admin_partner("Admin_Partner") is True
    assert omnihub_role_is_admin_partner("user") is False
    assert omnihub_role_is_admin_partner("agent") is False
    assert omnihub_role_is_admin_partner(None) is False


def test_resolve_chatwoot_account_role():
    assert resolve_chatwoot_account_role("admin-partner") == "administrator"
    assert resolve_chatwoot_account_role("admin_partner") == "administrator"
    assert resolve_chatwoot_account_role("admin") == "administrator"
    assert resolve_chatwoot_account_role("super-admin") == "administrator"
    assert resolve_chatwoot_account_role("user") == "agent"
    assert resolve_chatwoot_account_role("Agent") == "agent"
    assert resolve_chatwoot_account_role(None) == "agent"
    assert resolve_chatwoot_account_role("") == "agent"


def test_omnihub_role_is_platform_admin_name():
    from app.services.v1.handle_chatwoot.user_tokens import (
        omnihub_role_is_platform_admin_name,
    )

    assert omnihub_role_is_platform_admin_name("admin") is True
    assert omnihub_role_is_platform_admin_name("Admin") is True
    assert omnihub_role_is_platform_admin_name("admin-partner") is False
    assert omnihub_role_is_platform_admin_name("user") is False
