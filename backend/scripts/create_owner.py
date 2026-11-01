"""Create the owner account, or set a new password for it.

    python -m scripts.create_owner you@example.com            # prompts for the password
    python -m scripts.create_owner you@example.com --reset    # change the password

The password is read with getpass (never echoed, never an argument, so it stays out of shell
history) and stored only as an argon2id hash. Changing it signs out every existing session.
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import sys
from datetime import UTC, datetime

from sqlalchemy import select, update

from app.auth.passwords import MIN_LENGTH, hash_password
from app.auth.sessions import SessionService, normalize_email
from app.core.config import get_settings
from app.db.models import Session, User
from app.db.session import make_engine, make_session_factory


def read_password() -> str:
    first = getpass.getpass(f"Password (at least {MIN_LENGTH} characters): ")
    if getpass.getpass("Repeat it: ") != first:
        raise SystemExit("The passwords don't match.")
    return first


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("email")
    parser.add_argument("--reset", action="store_true", help="set a new password")
    args = parser.parse_args()
    email = normalize_email(args.email)

    engine = make_engine(get_settings().database_url)
    try:
        async with make_session_factory(engine)() as db, db.begin():
            user = await db.scalar(select(User).where(User.email == email))
            if user and not args.reset:
                print("That account exists; use --reset to change its password.", file=sys.stderr)
                return 1
            if not user and args.reset:
                print("No account with that email.", file=sys.stderr)
                return 1
            try:
                password = read_password()
                if user:
                    user.password_hash = hash_password(password)
                    await db.execute(
                        update(Session)
                        .where(Session.user_id == user.id, Session.revoked_at.is_(None))
                        .values(revoked_at=datetime.now(UTC))
                    )
                else:
                    await SessionService(db).create_owner(email, password)
            except ValueError as exc:
                print(f"Password rejected: {exc}.", file=sys.stderr)
                return 1
        print("Password updated; other sessions signed out." if user else "Owner account created.")
        return 0
    finally:
        await engine.dispose()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
