"""HostAuthService: extracts business logic from host_auth API endpoints.
- Compute consent scope unions from plugin manifests honoring subscriptions
- CRUD helpers for PluginSubscription with validation against PluginDefinition
- Read helpers for listing subscriptions

This keeps API controllers thin and maintains provider-agnostic behavior.
"""

from __future__ import annotations

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from ..models.plugin_subscription import PluginSubscription

logger = logging.getLogger(__name__)


class HostAuthService:
    @staticmethod
    async def _lookup_subscription(
        db: AsyncSession,
        *,
        user_id: str,
        provider: str,
        plugin_name: str,
        account_id: str | None = None,
    ):
        from sqlalchemy import and_, select  # local import

        prov = (provider or "").strip().lower()
        name = (plugin_name or "").strip()

        sel = await db.execute(
            select(PluginSubscription).where(
                and_(
                    PluginSubscription.user_id == str(user_id),
                    PluginSubscription.provider_key == prov,
                    PluginSubscription.account_id == (account_id or None),
                    PluginSubscription.plugin_name == name,
                )
            )
        )
        return prov, name, sel.scalars().first()

    @staticmethod
    def _log_subscription_event(
        action: str, *, user_id: str, provider: str, plugin_name: str, account_id: str | None
    ) -> None:
        try:
            logger.info(
                "subscription.%s | user=%s provider=%s plugin=%s account_id=%s",
                action,
                str(user_id),
                provider,
                plugin_name,
                (account_id or None),
            )
        except Exception:
            pass

    @staticmethod
    async def compute_consent_scopes(db: AsyncSession, user_id: str, provider: str) -> list[str]:
        """Compute union of delegated scopes for provider from plugin manifests, honoring subscriptions.
        If the user has no subscriptions for the provider, return an empty list (request nothing).
        """
        provider_key = (provider or "").strip().lower()
        union_scopes: list[str] = []
        if not provider_key:
            return union_scopes
        try:
            from sqlalchemy import and_, select  # local import to avoid circulars

            from ..models.plugin_registry import PluginDefinition
            from ..plugins.registry import REGISTRY

            # Load plugin manifest registry and enabled flags
            manifest = REGISTRY.get_manifest(refresh_if_empty=True) or {}
            res = await db.execute(select(PluginDefinition))
            rows = res.scalars().all()
            enabled_by_name = {r.name: bool(getattr(r, "enabled", False)) for r in rows}

            # Determine subscribed plugin names for this user/provider
            subs_res = await db.execute(
                select(PluginSubscription).where(
                    and_(
                        PluginSubscription.user_id == str(user_id),
                        PluginSubscription.provider_key == provider_key,
                    )
                )
            )
            subs = subs_res.scalars().all()
            subscribed_names = {s.plugin_name for s in subs}
            try:
                logger.debug(
                    "consent_scopes.compute | user=%s provider=%s subscribed=%s",
                    str(user_id),
                    provider_key,
                    bool(subscribed_names),
                )
            except Exception:
                pass

            for name, rec in manifest.items():
                if not enabled_by_name.get(name):
                    continue
                # Only include explicitly subscribed plugins; if none are subscribed, request nothing
                if not subscribed_names:
                    continue
                if name not in subscribed_names:
                    continue
                try:
                    op_auth = getattr(rec, "op_auth", None) or {}
                    if isinstance(op_auth, dict):
                        for spec in op_auth.values():
                            if not isinstance(spec, dict):
                                continue
                            if str(spec.get("provider") or "").strip().lower() != provider_key:
                                continue
                            if str(spec.get("mode") or "").strip().lower() != "user":
                                continue
                            scopes = spec.get("scopes")
                            if isinstance(scopes, list):
                                for s in scopes:
                                    sv = str(s).strip()
                                    if sv and sv not in union_scopes:
                                        union_scopes.append(sv)
                except Exception:
                    continue
        except Exception:
            # Log at caller
            pass
        return union_scopes

    @staticmethod
    async def list_subscriptions(db: AsyncSession, user_id: str, provider: str, account_id: str | None = None):
        from sqlalchemy import and_, select  # local import

        prov = (provider or "").strip().lower()
        q = select(PluginSubscription).where(
            and_(
                PluginSubscription.user_id == str(user_id),
                PluginSubscription.provider_key == prov,
            )
        )
        if account_id is not None:
            q = q.where(PluginSubscription.account_id == account_id)
        res = await db.execute(q)
        return res.scalars().all()

    @staticmethod
    async def validate_and_create_subscription(
        db: AsyncSession,
        user_id: str,
        provider: str,
        plugin_name: str,
        account_id: str | None = None,
    ):
        from sqlalchemy import select  # local import

        from ..models.plugin_registry import PluginDefinition

        prov = (provider or "").strip().lower()
        name = (plugin_name or "").strip()
        if not name:
            raise ValueError("plugin_name required")

        # Validate plugin exists and is enabled
        r = await db.execute(select(PluginDefinition).where(PluginDefinition.name == name))
        row = r.scalars().first()
        if not row or not bool(getattr(row, "enabled", False)):
            raise LookupError(f"Plugin '{name}' not found or disabled")

        # Upsert (idempotent)
        _, _, existing = await HostAuthService._lookup_subscription(
            db,
            user_id=user_id,
            provider=provider,
            plugin_name=plugin_name,
            account_id=account_id,
        )
        if existing:
            return existing

        rec = PluginSubscription(
            user_id=str(user_id),
            provider_key=prov,
            account_id=(account_id or None),
            plugin_name=name,
        )
        db.add(rec)
        await db.commit()
        HostAuthService._log_subscription_event(
            "created",
            user_id=str(user_id),
            provider=prov,
            plugin_name=name,
            account_id=account_id,
        )
        await db.refresh(rec)
        return rec

    @staticmethod
    async def delete_subscription(
        db: AsyncSession,
        user_id: str,
        provider: str,
        plugin_name: str,
        account_id: str | None = None,
    ) -> bool:
        prov = (provider or "").strip().lower()
        name = (plugin_name or "").strip()
        if not name:
            raise ValueError("plugin_name required")

        _, _, row = await HostAuthService._lookup_subscription(
            db,
            user_id=user_id,
            provider=provider,
            plugin_name=plugin_name,
            account_id=account_id,
        )
        if not row:
            return False
        try:
            await db.delete(row)
            await db.commit()
            HostAuthService._log_subscription_event(
                "deleted",
                user_id=str(user_id),
                provider=prov,
                plugin_name=name,
                account_id=account_id,
            )
            return True
        except Exception:
            await db.rollback()
            raise
