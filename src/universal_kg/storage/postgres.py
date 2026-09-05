from __future__ import annotations

from datetime import datetime
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import DateTime, Float, ForeignKey, Index, Integer, String, Text, or_, select, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from universal_kg.domain import Chunk, Document, Entity, Relationship, SearchHit

DATABASE_EMBEDDING_DIMENSIONS = 384


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
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        Index("documents_workspace_source_external_idx", "workspace_id", "source", "external_id"),
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
    embedding: Mapped[list[float]] = mapped_column(
        Vector(DATABASE_EMBEDDING_DIMENSIONS),
        nullable=False,
    )

    __table_args__ = (
        Index("chunks_workspace_document_ordinal_idx", "workspace_id", "document_id", "ordinal"),
    )


class EntityRecord(Base):
    __tablename__ = "entities"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(512), nullable=False)
    type: Mapped[str] = mapped_column(String(64), nullable=False)
    metadata_json: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, nullable=False)

    __table_args__ = (Index("entities_workspace_name_idx", "workspace_id", "name"),)


class RelationshipRecord(Base):
    __tablename__ = "relationships"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    subject: Mapped[str] = mapped_column(String(512), nullable=False)
    predicate: Mapped[str] = mapped_column(String(256), nullable=False)
    object_name: Mapped[str] = mapped_column("object", String(512), nullable=False)
    evidence_chunk_id: Mapped[str | None] = mapped_column(
        Text,
        ForeignKey("chunks.id", ondelete="SET NULL"),
    )
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    metadata_json: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, nullable=False)

    __table_args__ = (
        Index("relationships_workspace_subject_idx", "workspace_id", "subject"),
        Index("relationships_workspace_object_idx", "workspace_id", "object"),
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
        table = DocumentRecord.__table__
        statement = pg_insert(table).values(
            id=document.id,
            workspace_id=document.workspace_id,
            source=document.source,
            external_id=document.external_id,
            title=document.title,
            body=document.body,
            metadata=document.metadata,
            created_at=document.created_at,
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
                "created_at": statement.excluded.created_at,
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
                "embedding": vector,
            }
            for chunk, vector in zip(chunks, vectors, strict=True)
        ]
        table = ChunkRecord.__table__
        statement = pg_insert(table).values(values)
        statement = statement.on_conflict_do_update(
            index_elements=[table.c.id],
            set_={
                "document_id": statement.excluded.document_id,
                "workspace_id": statement.excluded.workspace_id,
                "text": statement.excluded.text,
                "ordinal": statement.excluded.ordinal,
                "metadata": statement.excluded.metadata,
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
                        "name": entity.name,
                        "type": str(entity.type),
                        "metadata": entity.metadata,
                    }
                    for entity in entities
                ]
                table = EntityRecord.__table__
                entity_statement = pg_insert(table).values(entity_values)
                entity_statement = entity_statement.on_conflict_do_update(
                    index_elements=[table.c.id],
                    set_={
                        "workspace_id": entity_statement.excluded.workspace_id,
                        "name": entity_statement.excluded.name,
                        "type": entity_statement.excluded.type,
                        "metadata": entity_statement.excluded.metadata,
                    },
                )
                await session.execute(entity_statement)

            if relationships:
                relationship_values = [
                    {
                        "id": relationship.id,
                        "workspace_id": relationship.workspace_id,
                        "subject": relationship.subject,
                        "predicate": relationship.predicate,
                        "object": relationship.object,
                        "evidence_chunk_id": relationship.evidence_chunk_id,
                        "confidence": relationship.confidence,
                        "metadata": relationship.metadata,
                    }
                    for relationship in relationships
                ]
                table = RelationshipRecord.__table__
                relationship_statement = pg_insert(table).values(relationship_values)
                relationship_statement = relationship_statement.on_conflict_do_update(
                    index_elements=[table.c.id],
                    set_={
                        "workspace_id": relationship_statement.excluded.workspace_id,
                        "subject": relationship_statement.excluded.subject,
                        "predicate": relationship_statement.excluded.predicate,
                        "object": relationship_statement.excluded.object,
                        "evidence_chunk_id": relationship_statement.excluded.evidence_chunk_id,
                        "confidence": relationship_statement.excluded.confidence,
                        "metadata": relationship_statement.excluded.metadata,
                    },
                )
                await session.execute(relationship_statement)

            await session.commit()

    async def search(
        self,
        workspace_id: str,
        query_vector: list[float],
        limit: int,
    ) -> list[SearchHit]:
        if len(query_vector) != DATABASE_EMBEDDING_DIMENSIONS:
            raise ValueError(
                f"embedding_dimension_mismatch:{len(query_vector)}:{DATABASE_EMBEDDING_DIMENSIONS}"
            )
        distance = ChunkRecord.embedding.cosine_distance(query_vector)
        statement = (
            select(DocumentRecord, ChunkRecord, distance.label("distance"))
            .join(DocumentRecord, DocumentRecord.id == ChunkRecord.document_id)
            .where(ChunkRecord.workspace_id == workspace_id)
            .order_by(distance)
            .limit(limit)
        )

        async with self._sessions() as session:
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
                )
            )
        return hits

    async def graph_context(
        self,
        workspace_id: str,
        query: str,
    ) -> tuple[list[Entity], list[Relationship]]:
        tokens = sorted({token.lower() for token in query.split() if len(token) > 2})
        if not tokens:
            return [], []

        entity_conditions = [EntityRecord.name.ilike(f"%{token}%") for token in tokens]
        entity_statement = (
            select(EntityRecord)
            .where(EntityRecord.workspace_id == workspace_id, or_(*entity_conditions))
            .limit(20)
        )

        async with self._sessions() as session:
            entity_rows = (await session.scalars(entity_statement)).all()
            names = [row.name for row in entity_rows]
            relationship_rows: list[RelationshipRecord] = []
            if names:
                relationship_statement = (
                    select(RelationshipRecord)
                    .where(
                        RelationshipRecord.workspace_id == workspace_id,
                        or_(
                            RelationshipRecord.subject.in_(names),
                            RelationshipRecord.object_name.in_(names),
                        ),
                    )
                    .limit(50)
                )
                relationship_rows = list(
                    (await session.scalars(relationship_statement)).all()
                )

        entities = [
            Entity(
                id=row.id,
                workspace_id=row.workspace_id,
                name=row.name,
                type=row.type,
                metadata=row.metadata_json,
            )
            for row in entity_rows
        ]
        relationships = [
            Relationship(
                id=row.id,
                workspace_id=row.workspace_id,
                subject=row.subject,
                predicate=row.predicate,
                object=row.object_name,
                evidence_chunk_id=row.evidence_chunk_id,
                confidence=row.confidence,
                metadata=row.metadata_json,
            )
            for row in relationship_rows
        ]
        return entities, relationships

    async def check_ready(self) -> None:
        async with self._engine.connect() as connection:
            await connection.execute(text("select 1"))
            version = await connection.execute(text("select version_num from alembic_version"))
            if version.scalar_one_or_none() is None:
                raise RuntimeError("database_schema_not_migrated")

    async def close(self) -> None:
        await self._engine.dispose()
