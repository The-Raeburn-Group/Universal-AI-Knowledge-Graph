from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, cast

from pgvector.sqlalchemy import Vector  # type: ignore[import-untyped]
from sqlalchemy import (
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Table,
    Text,
    delete,
    func,
    or_,
    select,
    text,
    update,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from universal_kg.domain import (
    AccessContext,
    AccessPolicy,
    Chunk,
    Document,
    Entity,
    Relationship,
    SearchHit,
    TombstoneDocumentResponse,
)

DATABASE_EMBEDDING_DIMENSIONS = 384
EXPECTED_ALEMBIC_REVISION = "20260925_0004"


def _policy_json(policy: AccessPolicy) -> dict[str, Any]:
    return policy.model_dump(mode="json")


def _source_acl_ref(access_json: dict[str, Any]) -> str | None:
    value = access_json.get("source_acl_ref")
    if isinstance(value, str):
        normalized = value.strip()
        if normalized:
            return normalized
    return None


def _access_filter(column: Any, access: AccessContext) -> Any:
    clauses = [column.contains({"visibility": "workspace"})]
    clauses.append(column.contains({"principals": [access.principal_id]}))
    clauses.extend(column.contains({"roles": [role]}) for role in access.roles)
    clauses.extend(column.contains({"groups": [group]}) for group in access.groups)
    return or_(*clauses)


class Base(DeclarativeBase):
    pass


class DocumentRecord(Base):
    __tablename__ = "documents"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    external_id: Mapped[str | None] = mapped_column(String(512))
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    metadata_json: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, nullable=False)
    access_json: Mapped[dict[str, Any]] = mapped_column("access", JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    retention_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    purge_after: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    deletion_reason: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        Index("documents_workspace_source_external_idx", "workspace_id", "source", "external_id"),
        Index("documents_access_gin_idx", "access", postgresql_using="gin"),
        Index("documents_workspace_retention_idx", "workspace_id", "retention_until"),
    )


class ChunkRecord(Base):
    __tablename__ = "chunks"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    document_id: Mapped[str] = mapped_column(
        Text,
        ForeignKey("documents.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    workspace_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    metadata_json: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, nullable=False)
    access_json: Mapped[dict[str, Any]] = mapped_column("access", JSONB, nullable=False)
    embedding: Mapped[list[float]] = mapped_column(
        Vector(DATABASE_EMBEDDING_DIMENSIONS),
        nullable=False,
    )

    __table_args__ = (
        Index("chunks_workspace_document_ordinal_idx", "workspace_id", "document_id", "ordinal"),
        Index("chunks_access_gin_idx", "access", postgresql_using="gin"),
    )


class EntityRecord(Base):
    __tablename__ = "entities"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    document_id: Mapped[str | None] = mapped_column(
        Text,
        ForeignKey("documents.id", ondelete="CASCADE"),
        index=True,
    )
    name: Mapped[str] = mapped_column(String(512), nullable=False)
    type: Mapped[str] = mapped_column(String(64), nullable=False)
    metadata_json: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, nullable=False)
    access_json: Mapped[dict[str, Any]] = mapped_column("access", JSONB, nullable=False)

    __table_args__ = (
        Index("entities_workspace_name_idx", "workspace_id", "name"),
        Index("entities_access_gin_idx", "access", postgresql_using="gin"),
    )


class RelationshipRecord(Base):
    __tablename__ = "relationships"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    document_id: Mapped[str | None] = mapped_column(
        Text,
        ForeignKey("documents.id", ondelete="CASCADE"),
        index=True,
    )
    subject: Mapped[str] = mapped_column(String(512), nullable=False)
    predicate: Mapped[str] = mapped_column(String(256), nullable=False)
    object_name: Mapped[str] = mapped_column("object", String(512), nullable=False)
    evidence_chunk_id: Mapped[str | None] = mapped_column(
        Text,
        ForeignKey("chunks.id", ondelete="SET NULL"),
    )
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    metadata_json: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, nullable=False)
    access_json: Mapped[dict[str, Any]] = mapped_column("access", JSONB, nullable=False)

    __table_args__ = (
        Index("relationships_workspace_subject_idx", "workspace_id", "subject"),
        Index("relationships_workspace_object_idx", "workspace_id", "object"),
        Index("relationships_access_gin_idx", "access", postgresql_using="gin"),
    )


class PostgresKnowledgeStore:
    def __init__(self, database_url: str, embedding_dimensions: int) -> None:
        if embedding_dimensions != DATABASE_EMBEDDING_DIMENSIONS:
            raise ValueError(
                "PostgreSQL embedding dimension mismatch: "
                f"configured={embedding_dimensions} schema={DATABASE_EMBEDDING_DIMENSIONS}. "
                "Changing vector dimensions requires a versioned database migration."
            )
        self._engine: AsyncEngine = create_async_engine(database_url, pool_pre_ping=True)
        self._sessions = async_sessionmaker(self._engine, expire_on_commit=False)

    async def upsert_document(self, document: Document) -> None:
        table = cast(Table, DocumentRecord.__table__)
        statement = pg_insert(table).values(
            id=document.id,
            workspace_id=document.workspace_id,
            source=document.source,
            external_id=document.external_id,
            title=document.title,
            body=document.body,
            metadata=document.metadata,
            access=_policy_json(document.access),
            created_at=document.created_at,
            retention_until=document.retention_until,
            deleted_at=document.deleted_at,
            purge_after=document.purge_after,
            deletion_reason=document.deletion_reason,
        )
        statement = statement.on_conflict_do_update(
            index_elements=[table.c.id],
            set_={
                "workspace_id": statement.excluded.workspace_id,
                "source": statement.excluded.source,
                "external_id": statement.excluded.external_id,
                "title": statement.excluded.title,
                "body": statement.excluded.body,
                "metadata": statement.excluded.metadata,
                "access": statement.excluded.access,
                "created_at": statement.excluded.created_at,
                "retention_until": statement.excluded.retention_until,
                "deleted_at": statement.excluded.deleted_at,
                "purge_after": statement.excluded.purge_after,
                "deletion_reason": statement.excluded.deletion_reason,
            },
        )
        async with self._sessions() as session:
            await session.execute(statement)
            await session.commit()

    async def upsert_chunks(self, chunks: list[Chunk], vectors: list[list[float]]) -> None:
        if len(chunks) != len(vectors):
            raise ValueError("chunk_vector_count_mismatch")
        if not chunks:
            return
        for vector in vectors:
            if len(vector) != DATABASE_EMBEDDING_DIMENSIONS:
                raise ValueError(
                    f"embedding_dimension_mismatch:{len(vector)}:{DATABASE_EMBEDDING_DIMENSIONS}"
                )

        values = [
            {
                "id": chunk.id,
                "document_id": chunk.document_id,
                "workspace_id": chunk.workspace_id,
                "text": chunk.text,
                "ordinal": chunk.ordinal,
                "metadata": chunk.metadata,
                "access": _policy_json(chunk.access),
                "embedding": vector,
            }
            for chunk, vector in zip(chunks, vectors, strict=True)
        ]
        table = cast(Table, ChunkRecord.__table__)
        statement = pg_insert(table).values(values)
        statement = statement.on_conflict_do_update(
            index_elements=[table.c.id],
            set_={
                "document_id": statement.excluded.document_id,
                "workspace_id": statement.excluded.workspace_id,
                "text": statement.excluded.text,
                "ordinal": statement.excluded.ordinal,
                "metadata": statement.excluded.metadata,
                "access": statement.excluded.access,
                "embedding": statement.excluded.embedding,
            },
        )
        async with self._sessions() as session:
            await session.execute(statement)
            await session.commit()

    async def upsert_graph(
        self,
        entities: list[Entity],
        relationships: list[Relationship],
    ) -> None:
        async with self._sessions() as session:
            if entities:
                entity_values = [
                    {
                        "id": entity.id,
                        "workspace_id": entity.workspace_id,
                        "document_id": entity.document_id,
                        "name": entity.name,
                        "type": str(entity.type),
                        "metadata": entity.metadata,
                        "access": _policy_json(entity.access),
                    }
                    for entity in entities
                ]
                table = cast(Table, EntityRecord.__table__)
                entity_statement = pg_insert(table).values(entity_values)
                entity_statement = entity_statement.on_conflict_do_update(
                    index_elements=[table.c.id],
                    set_={
                        "workspace_id": entity_statement.excluded.workspace_id,
                        "document_id": entity_statement.excluded.document_id,
                        "name": entity_statement.excluded.name,
                        "type": entity_statement.excluded.type,
                        "metadata": entity_statement.excluded.metadata,
                        "access": entity_statement.excluded.access,
                    },
                )
                await session.execute(entity_statement)

            if relationships:
                relationship_values = [
                    {
                        "id": relationship.id,
                        "workspace_id": relationship.workspace_id,
                        "document_id": relationship.document_id,
                        "subject": relationship.subject,
                        "predicate": relationship.predicate,
                        "object": relationship.object,
                        "evidence_chunk_id": relationship.evidence_chunk_id,
                        "confidence": relationship.confidence,
                        "metadata": relationship.metadata,
                        "access": _policy_json(relationship.access),
                    }
                    for relationship in relationships
                ]
                table = cast(Table, RelationshipRecord.__table__)
                relationship_statement = pg_insert(table).values(relationship_values)
                relationship_statement = relationship_statement.on_conflict_do_update(
                    index_elements=[table.c.id],
                    set_={
                        "workspace_id": relationship_statement.excluded.workspace_id,
                        "document_id": relationship_statement.excluded.document_id,
                        "subject": relationship_statement.excluded.subject,
                        "predicate": relationship_statement.excluded.predicate,
                        "object": relationship_statement.excluded.object,
                        "evidence_chunk_id": relationship_statement.excluded.evidence_chunk_id,
                        "confidence": relationship_statement.excluded.confidence,
                        "metadata": relationship_statement.excluded.metadata,
                        "access": relationship_statement.excluded.access,
                    },
                )
                await session.execute(relationship_statement)

            await session.commit()

    async def search(
        self,
        workspace_id: str,
        query_vector: list[float],
        limit: int,
        access: AccessContext,
    ) -> list[SearchHit]:
        if len(query_vector) != DATABASE_EMBEDDING_DIMENSIONS:
            raise ValueError(
                f"embedding_dimension_mismatch:{len(query_vector)}:{DATABASE_EMBEDDING_DIMENSIONS}"
            )
        distance = ChunkRecord.embedding.cosine_distance(query_vector)
        statement = (
            select(DocumentRecord, ChunkRecord, distance.label("distance"))
            .join(DocumentRecord, DocumentRecord.id == ChunkRecord.document_id)
            .where(
                ChunkRecord.workspace_id == workspace_id,
                DocumentRecord.workspace_id == workspace_id,
                DocumentRecord.deleted_at.is_(None),
                _access_filter(DocumentRecord.access_json, access),
                _access_filter(ChunkRecord.access_json, access),
            )
            .order_by(distance)
            .limit(limit)
        )

        async with self._sessions() as session:
            # pgvector 0.3.x HNSW chooses its approximate candidate set before
            # PostgreSQL applies selective ACL predicates. Force an exact vector
            # scan for permission-filtered retrieval so inaccessible neighbours
            # cannot suppress permitted evidence from the final top-k.
            await session.execute(text("SET LOCAL enable_indexscan = off"))
            rows = (await session.execute(statement)).all()

        hits: list[SearchHit] = []
        for document, chunk, distance_value in rows:
            hits.append(
                SearchHit(
                    document_id=document.id,
                    chunk_id=chunk.id,
                    title=document.title,
                    text=chunk.text,
                    score=1.0 - float(distance_value),
                    source=document.source,
                    metadata=document.metadata_json | chunk.metadata_json,
                    source_acl_ref=_source_acl_ref(document.access_json),
                )
            )
        return hits

    async def lexical_search(
        self,
        workspace_id: str,
        query: str,
        limit: int,
        access: AccessContext,
    ) -> list[SearchHit]:
        normalized = query.strip()
        if not normalized:
            return []

        config = text("'simple'::regconfig")
        document_vector = func.to_tsvector(config, ChunkRecord.text)
        tsquery = func.websearch_to_tsquery(config, normalized)
        rank = func.ts_rank_cd(document_vector, tsquery)
        statement = (
            select(DocumentRecord, ChunkRecord, rank.label("rank"))
            .join(DocumentRecord, DocumentRecord.id == ChunkRecord.document_id)
            .where(
                ChunkRecord.workspace_id == workspace_id,
                DocumentRecord.workspace_id == workspace_id,
                DocumentRecord.deleted_at.is_(None),
                _access_filter(DocumentRecord.access_json, access),
                _access_filter(ChunkRecord.access_json, access),
                document_vector.op("@@")(tsquery),
            )
            .order_by(rank.desc(), ChunkRecord.id.asc())
            .limit(limit)
        )

        async with self._sessions() as session:
            rows = (await session.execute(statement)).all()

        return [
            SearchHit(
                document_id=document.id,
                chunk_id=chunk.id,
                title=document.title,
                text=chunk.text,
                score=float(rank_value),
                source=document.source,
                metadata=document.metadata_json | chunk.metadata_json,
                source_acl_ref=_source_acl_ref(document.access_json),
            )
            for document, chunk, rank_value in rows
        ]

    async def graph_context(
        self,
        workspace_id: str,
        query: str,
        access: AccessContext,
        depth: int = 1,
    ) -> tuple[list[Entity], list[Relationship]]:
        if depth < 0 or depth > 3:
            raise ValueError("graph_depth_out_of_range")
        tokens = sorted({token.lower() for token in query.split() if len(token) > 2})
        if not tokens or depth == 0:
            return [], []

        entity_conditions = [EntityRecord.name.ilike(f"%{token}%") for token in tokens]
        seed_statement = (
            select(EntityRecord)
            .join(DocumentRecord, DocumentRecord.id == EntityRecord.document_id)
            .where(
                EntityRecord.workspace_id == workspace_id,
                DocumentRecord.workspace_id == workspace_id,
                DocumentRecord.deleted_at.is_(None),
                _access_filter(DocumentRecord.access_json, access),
                _access_filter(EntityRecord.access_json, access),
                or_(*entity_conditions),
            )
            .order_by(EntityRecord.name.asc(), EntityRecord.id.asc())
            .limit(20)
        )

        entity_rows: dict[str, EntityRecord] = {}
        relationship_rows: dict[str, RelationshipRecord] = {}
        async with self._sessions() as session:
            seeds = list((await session.scalars(seed_statement)).all())
            for row in seeds:
                entity_rows[row.id] = row
            frontier = {row.name for row in seeds}

            for _ in range(depth):
                if not frontier or len(relationship_rows) >= 50:
                    break
                remaining = 50 - len(relationship_rows)
                relationship_statement = (
                    select(RelationshipRecord)
                    .join(DocumentRecord, DocumentRecord.id == RelationshipRecord.document_id)
                    .where(
                        RelationshipRecord.workspace_id == workspace_id,
                        DocumentRecord.workspace_id == workspace_id,
                        DocumentRecord.deleted_at.is_(None),
                        _access_filter(DocumentRecord.access_json, access),
                        _access_filter(RelationshipRecord.access_json, access),
                        or_(
                            RelationshipRecord.subject.in_(sorted(frontier)),
                            RelationshipRecord.object_name.in_(sorted(frontier)),
                        ),
                    )
                )
                if relationship_rows:
                    relationship_statement = relationship_statement.where(
                        ~RelationshipRecord.id.in_(sorted(relationship_rows))
                    )
                relationship_statement = (
                    relationship_statement.order_by(
                        RelationshipRecord.subject.asc(),
                        RelationshipRecord.predicate.asc(),
                        RelationshipRecord.object_name.asc(),
                        RelationshipRecord.id.asc(),
                    ).limit(remaining)
                )
                discovered_relationships = list(
                    (await session.scalars(relationship_statement)).all()
                )
                if not discovered_relationships:
                    break

                connected_names: set[str] = set()
                for relationship in discovered_relationships:
                    relationship_rows.setdefault(relationship.id, relationship)
                    connected_names.add(relationship.subject)
                    connected_names.add(relationship.object_name)

                known_names = {row.name for row in entity_rows.values()}
                new_names = connected_names - known_names
                if not new_names:
                    frontier = set()
                    continue

                remaining_entities = 20 - len(entity_rows)
                if remaining_entities <= 0:
                    break
                entity_statement = (
                    select(EntityRecord)
                    .join(DocumentRecord, DocumentRecord.id == EntityRecord.document_id)
                    .where(
                        EntityRecord.workspace_id == workspace_id,
                        DocumentRecord.workspace_id == workspace_id,
                        DocumentRecord.deleted_at.is_(None),
                        _access_filter(DocumentRecord.access_json, access),
                        _access_filter(EntityRecord.access_json, access),
                        EntityRecord.name.in_(sorted(new_names)),
                    )
                    .order_by(EntityRecord.name.asc(), EntityRecord.id.asc())
                    .limit(remaining_entities)
                )
                discovered_entities = list(
                    (await session.scalars(entity_statement)).all()
                )
                for row in discovered_entities:
                    entity_rows.setdefault(row.id, row)
                frontier = {row.name for row in discovered_entities}

        entities = [
            Entity(
                id=row.id,
                workspace_id=row.workspace_id,
                document_id=row.document_id,
                name=row.name,
                type=row.type,
                metadata=row.metadata_json,
                access=AccessPolicy.model_validate(row.access_json),
            )
            for row in entity_rows.values()
        ]
        relationships = [
            Relationship(
                id=row.id,
                workspace_id=row.workspace_id,
                document_id=row.document_id,
                subject=row.subject,
                predicate=row.predicate,
                object=row.object_name,
                evidence_chunk_id=row.evidence_chunk_id,
                confidence=row.confidence,
                metadata=row.metadata_json,
                access=AccessPolicy.model_validate(row.access_json),
            )
            for row in relationship_rows.values()
        ]
        return entities[:20], relationships[:50]

    async def tombstone_document(
        self,
        workspace_id: str,
        document_id: str,
        reason: str,
        deleted_at: datetime,
        purge_after: datetime,
    ) -> TombstoneDocumentResponse | None:
        async with self._sessions() as session, session.begin():
            statement = (
                select(DocumentRecord)
                .where(
                    DocumentRecord.workspace_id == workspace_id,
                    DocumentRecord.id == document_id,
                )
                .with_for_update()
            )
            document = (await session.scalars(statement)).one_or_none()
            if document is None:
                return None
            if document.deleted_at is None:
                document.deleted_at = deleted_at
                document.purge_after = purge_after
                document.deletion_reason = reason
            return TombstoneDocumentResponse(
                document_id=document.id,
                workspace_id=document.workspace_id,
                deleted_at=document.deleted_at or deleted_at,
                purge_after=document.purge_after or purge_after,
                reason=document.deletion_reason or reason,
            )

    async def run_retention(
        self,
        workspace_id: str,
        as_of: datetime,
        purge_grace_days: int,
    ) -> tuple[int, int]:
        async with self._sessions() as session, session.begin():
            expired_statement = select(DocumentRecord.id).where(
                DocumentRecord.workspace_id == workspace_id,
                DocumentRecord.deleted_at.is_(None),
                DocumentRecord.retention_until.is_not(None),
                DocumentRecord.retention_until <= as_of,
            )
            expired_ids = list((await session.scalars(expired_statement)).all())
            if expired_ids:
                await session.execute(
                    update(DocumentRecord)
                    .where(DocumentRecord.id.in_(expired_ids))
                    .values(
                        deleted_at=as_of,
                        purge_after=as_of + timedelta(days=purge_grace_days),
                        deletion_reason="retention_expired",
                    )
                )

            purge_statement = select(DocumentRecord.id).where(
                DocumentRecord.workspace_id == workspace_id,
                DocumentRecord.deleted_at.is_not(None),
                DocumentRecord.purge_after.is_not(None),
                DocumentRecord.purge_after <= as_of,
            )
            purge_ids = list((await session.scalars(purge_statement)).all())
            if purge_ids:
                await session.execute(
                    delete(DocumentRecord).where(DocumentRecord.id.in_(purge_ids))
                )

        return len(expired_ids), len(purge_ids)

    async def check_ready(self) -> None:
        async with self._engine.connect() as connection:
            await connection.execute(text("select 1"))
            version_result = await connection.execute(
                text("select version_num from alembic_version")
            )
            current_revision = version_result.scalar_one_or_none()
            if current_revision != EXPECTED_ALEMBIC_REVISION:
                raise RuntimeError(
                    "database_schema_revision_mismatch:"
                    f"{current_revision or 'none'}:{EXPECTED_ALEMBIC_REVISION}"
                )

    async def close(self) -> None:
        await self._engine.dispose()
