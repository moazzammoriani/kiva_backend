"""CLI script to create or reset an admin user for the CMS dashboard.

Usage:
    uv run python create_admin.py <username> <password>
"""

import sys
import bcrypt
from database import init_db, SessionLocal, AdminUser


def main():
    if len(sys.argv) != 3:
        print(f"Usage: {sys.argv[0]} <username> <password>")
        sys.exit(1)

    username, password = sys.argv[1], sys.argv[2]
    password_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()

    init_db()
    db = SessionLocal()
    try:
        existing = db.query(AdminUser).filter(AdminUser.username == username).first()
        if existing:
            existing.password_hash = password_hash
            print(f"Updated password for existing user '{username}'")
        else:
            db.add(AdminUser(username=username, password_hash=password_hash))
            print(f"Created admin user '{username}'")
        db.commit()
    finally:
        db.close()


if __name__ == "__main__":
    main()
