"""Find, link or create the account for a Google identity."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.google import GoogleIdentity, GoogleSignInError
from app.auth.sessions import normalize_email
from app.db.models import User
from app.execution.audit import audit


async def account_for_google(
    db: AsyncSession, identity: GoogleIdentity, *, open_signup: bool
) -> User:
    """Google's subject ID is the stable key. An existing account with the same verified email
    and no Google link yet is linked on first sign-in (this is how the original owner account
    moves to Google). New accounts are created only while sign-up is open."""
    user = await db.scalar(select(User).where(User.google_sub == identity.sub))
    if user is not None:
        if identity.name and user.name != identity.name:
            user.name = identity.name
        return user

    email = normalize_email(identity.email)
    user = await db.scalar(select(User).where(User.email == email))
    if user is not None:
        if user.google_sub is not None:
            # The email belongs to a different Google account than the one linked.
            raise GoogleSignInError("account_conflict", "this email is linked to another account")
        user.google_sub = identity.sub
        user.name = user.name or identity.name
        audit(db, actor="owner", event="auth.google_linked", entity_type="user",
              entity_id=str(user.id))  # fmt: skip
        return user

    if not open_signup:
        raise GoogleSignInError("signup_closed", "new accounts aren't open yet")
    user = User(email=email, google_sub=identity.sub, name=identity.name, password_hash=None)
    db.add(user)
    await db.flush()
    audit(db, actor="owner", event="auth.account_created", entity_type="user",
          entity_id=str(user.id))  # fmt: skip
    return user
