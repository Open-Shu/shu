import uuid
from integ.response_utils import extract_data

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

