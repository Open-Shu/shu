#!/usr/bin/env python3
"""
Generate a test authentication token for debugging.
"""

import asyncio
import sys
import os

# Add the project root to the path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from shu.core.database import get_db_session
from shu.auth.jwt_manager import JWTManager
from sqlalchemy import text

async def generate_test_token():
    """Generate a test token for the admin user."""
    db = await get_db_session()
    
    try:
        # Get the admin user
        result = await db.execute(text(
            "SELECT id, email, name, role FROM users WHERE role = 'admin' LIMIT 1"
        ))
        user = result.fetchone()
        
        if not user:
            print("No admin user found in database")
            return
        
        print(f"Found admin user: {user[2]} ({user[1]})")
        
        # Create a token for this user
        jwt_manager = JWTManager()
        token_data = {
            "user_id": user[0],  # user ID
            "email": user[1],
            "role": user[3]
        }

        token = jwt_manager.create_access_token(token_data)
        
        print(f"\nGenerated test token:")
        print(f"Token: {token}")
        print(f"\nTo use this token:")
        print(f"1. Open browser Developer Tools (F12)")
        print(f"2. Go to Application → Local Storage → http://localhost:3000")
        print(f"3. Set 'shu_token' = '{token}'")
        print(f"4. Refresh the page")
        
        # Test the token
        print(f"\nTesting token with curl:")
        print(f"curl -X GET 'http://localhost:8000/api/v1/llm/providers' \\")
        print(f"  -H 'Authorization: Bearer {token}' \\")
        print(f"  -H 'accept: application/json'")
        
    except Exception as e:
        print(f"Error generating token: {e}")
        import traceback
        traceback.print_exc()
    finally:
        await db.close()

if __name__ == "__main__":
    asyncio.run(generate_test_token())
