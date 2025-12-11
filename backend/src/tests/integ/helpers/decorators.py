import functools

from sqlalchemy import text

from shu.auth.models import User
from shu.auth.jwt_manager import JWTManager


def replace_auth_headers_for_user(user_data):
    """
    Provider a generator that does the same as the integration_test_runner `_create_admin_user` function. We are not
    limited to admin users, as long as the input dict is valid. To achieve this we are replacing the regular admin
    user `auth_headers` with the newly generated ones.
    """

    def decorator(func):
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            # db is the second argument to the function
            db = args[1]

            await db.execute(text(f"DELETE FROM users WHERE email LIKE '{user_data.get('email')}'"))
            await db.commit()

            user = User(**user_data)
            db.add(user)
            await db.commit()
            await db.refresh(user)

            jwt_manager = JWTManager()
            token_data = {
                "user_id": user.id,
                "email": user.email,
                "role": user.role
            }
            admin_token = jwt_manager.create_access_token(token_data)

            # we are replacing the auth_headers parameter with the new access token
            return await func(
                *(
                    args[0],
                    args[1],
                    {"Authorization": f"Bearer {admin_token}", "_user_id": user.id}
                ),
                **kwargs
            )
        return wrapper
    return decorator
