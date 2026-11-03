"""Accounts: find, link or create the account for a Google identity; delete an account."""

from __future__ import annotations

import uuid

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.google import GoogleIdentity, GoogleSignInError
from app.auth.sessions import normalize_email
from app.db.models import (
    AccountSettings,
    ActionExecution,
    ActionProposal,
    AgentRun,
    AIAccess,
    AIUsage,
    CronRun,
    MemoryFact,
    NotionConnection,
    OAuthState,
    Session,
    User,
    Workspace,
)
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
        audit(db, actor="user", event="auth.google_linked", user_id=user.id,
              entity_type="user", entity_id=str(user.id))  # fmt: skip
        return user

    if not open_signup:
        raise GoogleSignInError("signup_closed", "new accounts aren't open yet")
    user = User(email=email, google_sub=identity.sub, name=identity.name, password_hash=None)
    db.add(user)
    await db.flush()
    audit(db, actor="user", event="auth.account_created", user_id=user.id,
          entity_type="user", entity_id=str(user.id))  # fmt: skip
    return user


async def account_threads(db: AsyncSession, user_id: uuid.UUID) -> list[str]:
    """The checkpoint threads of the account's runs, to delete before the account itself."""
    return list(await db.scalars(select(AgentRun.thread_id).where(AgentRun.user_id == user_id)))


async def delete_account(db: AsyncSession, user_id: uuid.UUID) -> None:
    """Removes everything stored for the account, in one transaction. Its audit entries go
    through `purge_user_audit`, the only way to delete from the append-only log. What remains
    is a single entry saying that an account (by ID only) was deleted."""
    await db.execute(select(func.purge_user_audit(user_id)))
    proposals = select(ActionProposal.id).where(ActionProposal.user_id == user_id)
    await db.execute(delete(ActionExecution).where(ActionExecution.proposal_id.in_(proposals)))
    await db.execute(delete(ActionProposal).where(ActionProposal.user_id == user_id))
    await db.execute(delete(CronRun).where(CronRun.user_id == user_id))
    await db.execute(delete(AgentRun).where(AgentRun.user_id == user_id))
    await db.execute(delete(Session).where(Session.user_id == user_id))
    await db.execute(delete(OAuthState).where(OAuthState.user_id == user_id))
    await db.execute(delete(NotionConnection).where(NotionConnection.user_id == user_id))
    await db.execute(delete(Workspace).where(Workspace.user_id == user_id))
    await db.execute(delete(AIAccess).where(AIAccess.user_id == user_id))
    await db.execute(delete(AIUsage).where(AIUsage.user_id == user_id))
    await db.execute(delete(AccountSettings).where(AccountSettings.user_id == user_id))
    await db.execute(delete(MemoryFact).where(MemoryFact.user_id == user_id))
    await db.execute(delete(User).where(User.id == user_id))
    audit(db, actor="user", event="account.deleted", user_id=None, entity_type="user",
          entity_id=str(user_id))  # fmt: skip
