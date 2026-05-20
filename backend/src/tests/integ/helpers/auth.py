import logging
import uuid

from integ.response_utils import extract_data

logger = logging.getLogger(__name__)


async def create_active_user_headers(client, admin_headers, role="regular_user"):
    """
    Create an active password-authenticated user via the admin API and return
    Authorization headers for that user. Standard helper for integration tests.
    """
    email = f"test_{uuid.uuid4().hex[:8]}@example.com"
    password = "Password123!"

    # Create active user
    create = await client.post(
        "/api/v1/auth/users",
        json={
            "email": email,
            "password": password,
            "name": "Test User",
            "role": role,
            "auth_method": "password",
        },
        headers=admin_headers,
    )
    assert create.status_code == 200, create.text

    # Login as created user
    login = await client.post(
        "/api/v1/auth/login/password",
        json={"email": email, "password": password},
    )
    assert login.status_code == 200, login.text
    token = extract_data(login)["access_token"]
    return {"Authorization": f"Bearer {token}"}


async def create_active_user_with_id(client, admin_headers, role="regular_user") -> tuple[dict, str]:
    """Create an active password-auth user and return (headers, user_id).

    Same as ``create_active_user_headers`` but also looks up the new user's
    id via ``/api/v1/auth/me`` so the caller can delete the user in a
    try/finally. This pairs with ``cleanup_test_user`` below.

    Use this when a test wants explicit teardown — the framework's
    automatic ``_cleanup_test_users`` does not reliably match the
    ``test_<uuid>@example.com`` email pattern this helper generates (the
    ``_`` after ``test`` blocks the ``\\b[Tt]est\\b`` word-boundary regex),
    so users created here can otherwise orphan and eat Stripe seats.
    """
    headers = await create_active_user_headers(client, admin_headers, role=role)
    me = await client.get("/api/v1/auth/me", headers=headers)
    assert me.status_code == 200, me.text
    user_id = extract_data(me)["user_id"]
    return headers, user_id


async def cleanup_test_user(client, admin_headers, user_id: str) -> None:
    """Best-effort delete of a test user via the admin DELETE endpoint.

    Swallows errors and logs them at WARNING — teardown should never mask
    the test's actual outcome with a cleanup failure. Pair with
    ``create_active_user_with_id`` in a ``try/finally``.
    """
    try:
        resp = await client.delete(f"/api/v1/auth/users/{user_id}", headers=admin_headers)
        if resp.status_code not in (200, 204):
            logger.warning("cleanup_test_user: DELETE returned %d for %s: %s", resp.status_code, user_id, resp.text)
    except Exception as exc:
        logger.warning("cleanup_test_user: error deleting %s: %s", user_id, exc)


async def cleanup_framework_test_admin(db) -> None:
    """End-of-suite teardown for the framework-created test-admin user.

    Mirrors the SQL the framework itself uses in ``_create_admin_user`` to
    clean up prior runs ([integration_test_runner.py:167-178]) — admin
    users can't delete themselves via the API
    (``user_service.delete_user`` blocks self-deletion), so the framework
    deletes by raw SQL and we do the same here.

    Without this, each suite run leaves its ``test-admin-<uuid>@example.com``
    behind until the next suite's setup deletes it. Calling this from a
    sentinel test that runs last in ``get_test_functions()`` keeps the DB
    clean after each individual suite run.

    Best-effort: errors are logged, never raised.
    """
    from sqlalchemy import text

    try:
        await db.execute(
            text(
                "DELETE FROM plugin_subscriptions WHERE user_id IN "
                "(SELECT id FROM users WHERE email LIKE 'test-admin-%@example.com')"
            )
        )
        await db.execute(
            text(
                "DELETE FROM provider_credentials WHERE user_id IN "
                "(SELECT id FROM users WHERE email LIKE 'test-admin-%@example.com')"
            )
        )
        await db.execute(text("DELETE FROM users WHERE email LIKE 'test-admin-%@example.com'"))
        await db.commit()
    except Exception as exc:
        logger.warning("cleanup_framework_test_admin: SQL delete failed: %s", exc)
