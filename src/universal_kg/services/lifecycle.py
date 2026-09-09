from __future__ import annotations

from datetime import UTC, datetime, timedelta

from universal_kg.config import get_settings
from universal_kg.domain import RetentionRunResponse, TombstoneDocumentResponse
from universal_kg.storage.base import KnowledgeStore
from universal_kg.storage.factory import get_knowledge_store


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("lifecycle timestamps must include a timezone")
    return value


class LifecycleService:
    def __init__(
        self,
        knowledge_store: KnowledgeStore | None = None,
        purge_grace_days: int | None = None,
    ) -> None:
        self.knowledge_store = knowledge_store or get_knowledge_store()
        configured_grace = get_settings().retention_purge_grace_days
        self.purge_grace_days = configured_grace if purge_grace_days is None else purge_grace_days
        if self.purge_grace_days < 0:
            raise ValueError("purge_grace_days must be >= 0")

    async def tombstone(
        self,
        workspace_id: str,
        document_id: str,
        reason: str,
        *,
        now: datetime | None = None,
    ) -> TombstoneDocumentResponse | None:
        deleted_at = _aware(now or datetime.now(UTC))
        purge_after = deleted_at + timedelta(days=self.purge_grace_days)
        return await self.knowledge_store.tombstone_document(
            workspace_id,
            document_id,
            reason,
            deleted_at,
            purge_after,
        )

    async def run_retention(
        self,
        workspace_id: str,
        *,
        as_of: datetime | None = None,
    ) -> RetentionRunResponse:
        effective_as_of = _aware(as_of or datetime.now(UTC))
        tombstoned, purged = await self.knowledge_store.run_retention(
            workspace_id,
            effective_as_of,
            self.purge_grace_days,
        )
        return RetentionRunResponse(
            workspace_id=workspace_id,
            as_of=effective_as_of,
            tombstoned=tombstoned,
            purged=purged,
        )
