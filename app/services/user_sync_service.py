"""UserSyncService — mirrors Clerk user state into local tables.

Driven by:
- The Clerk webhook (primary, proactive path).
- The `get_current_user` defensive fallback in app/core/security.py (covers
  webhook lag / brand-new envs without subscribed webhooks).

Idempotency:
- `user.created` re-delivered for an existing user → falls through to
  the update path. No duplicate `users` row (UNIQUE on `clerk_user_id`).
- `user.updated` for an unknown user → creates it (Clerk may emit update
  before create if a race occurred at their side; same effective state).
- `user.deleted` re-delivered → second call is a no-op (already soft-deleted).

Role sync:
- Reads single role from `public_metadata.role` (defaults to `customer`).
- Mirrored into `user_roles` junction. Multi-role support is a JSON array
  swap in `ClerkUser.role` + this service; the table is already many-to-many.
- The `roles` table is seeded by migration 0002 (`customer`, `admin`, `staff`);
  unknown role names are auto-created with `is_system=false`.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import delete, select

from app.core.logging import get_logger
from app.models.role import Role, UserRole
from app.models.user import User
from app.models.user_profile import UserProfile
from app.schemas.clerk_webhook import ClerkUser
from app.services.base import BaseService

log = get_logger(__name__)


class UserSyncService(BaseService):
    # ------------------------------------------------------------------
    # Public entry points
    # ------------------------------------------------------------------

    async def handle_user_created(self, clerk_user: ClerkUser) -> User:
        existing = await self._find_by_clerk_id(clerk_user.id)
        if existing is not None:
            log.info(
                "clerk_user_created_idempotent",
                clerk_user_id=clerk_user.id,
                local_user_id=str(existing.id),
            )
            return await self._apply_update(existing, clerk_user)
        return await self._create(clerk_user)

    async def handle_user_updated(self, clerk_user: ClerkUser) -> User:
        existing = await self._find_by_clerk_id(clerk_user.id)
        if existing is None:
            log.warning(
                "clerk_user_updated_missing_local",
                clerk_user_id=clerk_user.id,
            )
            return await self._create(clerk_user)
        return await self._apply_update(existing, clerk_user)

    async def handle_user_deleted(self, clerk_user_id: str) -> None:
        """Soft-delete only: scrub PII but keep row so `order.user_id` survives.

        See AUTH_FLOW.md §5: hard-deleting users would orphan FK references
        from `orders`, `audit_logs`, `stock_movements`, etc.
        """
        user = await self._find_by_clerk_id(clerk_user_id)
        if user is None:
            log.info("clerk_user_deleted_missing_local", clerk_user_id=clerk_user_id)
            return
        if user.deleted_at is not None:
            log.info("clerk_user_deleted_idempotent", clerk_user_id=clerk_user_id)
            return

        # PII scrub. The email gets a placeholder so the UNIQUE(email) constraint
        # doesn't block a future user with the same address.
        scrub_token = f"deleted+{clerk_user_id}@oorvashee.invalid"
        user.deleted_at = datetime.now(UTC)
        user.email = scrub_token
        user.phone = None

        # Profile cascade: scrub display fields too. Query explicitly to avoid
        # the async-session lazy-load trap.
        profile = (
            await self.session.execute(
                select(UserProfile).where(UserProfile.user_id == user.id)
            )
        ).scalar_one_or_none()
        if profile is not None:
            profile.full_name = None
            profile.display_name = None
            profile.avatar_url = None
            profile.date_of_birth = None
            profile.gender = None
            profile.preferences = {}

        # Roles: revoke everything.
        await self.session.execute(
            delete(UserRole).where(UserRole.user_id == user.id)
        )

        await self.session.flush()
        log.info(
            "clerk_user_soft_deleted",
            clerk_user_id=clerk_user_id,
            local_user_id=str(user.id),
        )

    # ------------------------------------------------------------------
    # Internal — create / update
    # ------------------------------------------------------------------

    async def _create(self, clerk_user: ClerkUser) -> User:
        email = clerk_user.primary_email
        if not email:
            # Without an email we can't satisfy the NOT NULL + UNIQUE constraint
            # on `users.email`. Skip and log loudly — better than poisoning the
            # table with junk placeholder addresses.
            log.error(
                "clerk_user_create_missing_email",
                clerk_user_id=clerk_user.id,
            )
            raise ValueError("Clerk user payload has no email address")

        user = User(
            clerk_user_id=clerk_user.id,
            email=email,
            phone=clerk_user.primary_phone,
        )
        self.session.add(user)
        await self.session.flush()

        await self._upsert_profile(user, clerk_user)
        await self._sync_role(user, clerk_user.role)
        await self.session.flush()

        log.info(
            "clerk_user_created",
            clerk_user_id=clerk_user.id,
            local_user_id=str(user.id),
            role=clerk_user.role,
        )
        return user

    async def _apply_update(self, user: User, clerk_user: ClerkUser) -> User:
        if clerk_user.primary_email:
            user.email = clerk_user.primary_email
        user.phone = clerk_user.primary_phone

        # If Clerk has revived a previously-deleted user (rare but possible
        # via dashboard restore), clear the soft-delete marker.
        if user.deleted_at is not None:
            user.deleted_at = None

        await self._upsert_profile(user, clerk_user)
        await self._sync_role(user, clerk_user.role)
        await self.session.flush()

        log.info(
            "clerk_user_updated",
            clerk_user_id=clerk_user.id,
            local_user_id=str(user.id),
            role=clerk_user.role,
        )
        return user

    # ------------------------------------------------------------------
    # Internal — profile + roles
    # ------------------------------------------------------------------

    async def _upsert_profile(self, user: User, clerk_user: ClerkUser) -> None:
        """Query the profile row explicitly.

        We deliberately avoid `user.profile` access because async sessions
        forbid implicit lazy loads. Even if `_find_by_clerk_id` eager-loads
        the relationship, a freshly-INSERTed `user` from `_create` has no
        loaded `profile` and accessing the attribute would trigger one.
        """
        existing = (
            await self.session.execute(
                select(UserProfile).where(UserProfile.user_id == user.id)
            )
        ).scalar_one_or_none()

        if existing is None:
            self.session.add(
                UserProfile(
                    user_id=user.id,
                    full_name=clerk_user.full_name,
                    avatar_url=clerk_user.image_url,
                )
            )
            return

        if clerk_user.full_name is not None:
            existing.full_name = clerk_user.full_name
        if clerk_user.image_url is not None:
            existing.avatar_url = clerk_user.image_url

    async def _sync_role(self, user: User, role_name: str) -> None:
        """Mirror Clerk's single role into `user_roles`.

        Strategy: reconcile to exactly one row for this user. Other roles
        present locally but not in Clerk are removed; the one in Clerk is
        added if missing. This keeps the local table as a faithful mirror
        of Clerk's `public_metadata.role`.
        """
        target_role = await self._get_or_create_role(role_name)

        existing_rows = (
            await self.session.execute(
                select(UserRole).where(UserRole.user_id == user.id)
            )
        ).scalars().all()

        keep_id = target_role.id
        to_delete_ids: list[Any] = [
            ur.id for ur in existing_rows if ur.role_id != keep_id
        ]
        if to_delete_ids:
            await self.session.execute(
                delete(UserRole).where(UserRole.id.in_(to_delete_ids))
            )

        already_has = any(ur.role_id == keep_id for ur in existing_rows)
        if not already_has:
            self.session.add(UserRole(user_id=user.id, role_id=target_role.id))

    async def _get_or_create_role(self, role_name: str) -> Role:
        stmt = select(Role).where(Role.name == role_name)
        role = (await self.session.execute(stmt)).scalar_one_or_none()
        if role is not None:
            return role
        # Unknown role from Clerk — auto-create as non-system so an admin
        # can later drop it without confusion.
        role = Role(name=role_name, is_system=False)
        self.session.add(role)
        await self.session.flush()
        log.info("clerk_role_autocreated", name=role_name)
        return role

    # ------------------------------------------------------------------
    # Lookups
    # ------------------------------------------------------------------

    async def _find_by_clerk_id(self, clerk_user_id: str) -> User | None:
        stmt = select(User).where(User.clerk_user_id == clerk_user_id)
        return (await self.session.execute(stmt)).scalar_one_or_none()
