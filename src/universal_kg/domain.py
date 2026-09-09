from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class SourceType(StrEnum):
    MANUAL = "manual"
    PDF = "pdf"
    EMAIL = "email"
    SLACK = "slack"
    CRM = "crm"
    GITHUB = "github"
    DATABASE = "database"
    JSON = "json"
    CSV = "csv"


class EntityType(StrEnum):
    PERSON = "person"
    ORGANISATION = "organisation"
    PRODUCT = "product"
    PROJECT = "project"
    SYSTEM = "system"
    CONCEPT = "concept"
    DOCUMENT = "document"
    ISSUE = "issue"
    DATABASE_TABLE = "database_table"


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


def _normalise_acl_values(values: list[str]) -> list[str]:
    normalised: set[str] = set()
    for value in values:
        item = value.strip().lower()
        if not item:
            continue
        if len(item) > 256:
            raise ValueError("access-control identifiers must be <= 256 characters")
        normalised.add(item)
    return sorted(normalised)


class AccessPolicy(StrictModel):
    visibility: Literal["workspace", "restricted"] = "workspace"
    principals: list[str] = Field(default_factory=list, max_length=200)
    roles: list[str] = Field(default_factory=list, max_length=100)
    groups: list[str] = Field(default_factory=list, max_length=200)
    source_acl_ref: str | None = Field(default=None, max_length=512)

    @field_validator("principals", "roles", "groups")
    @classmethod
    def normalise_subjects(cls, values: list[str]) -> list[str]:
        return _normalise_acl_values(values)

    @model_validator(mode="after")
    def validate_restricted_policy(self) -> AccessPolicy:
        if self.visibility == "restricted" and not (
            self.principals or self.roles or self.groups
        ):
            raise ValueError(
                "restricted access requires at least one principal, role or group"
            )
        return self


class AccessContext(StrictModel):
    workspace_id: str = Field(min_length=1, max_length=128, pattern=r"^[a-zA-Z0-9_.:-]+$")
    principal_id: str = Field(min_length=1, max_length=256)
    roles: list[str] = Field(default_factory=list, max_length=100)
    groups: list[str] = Field(default_factory=list, max_length=200)

    @field_validator("principal_id")
    @classmethod
    def normalise_principal(cls, value: str) -> str:
        return value.strip().lower()

    @field_validator("roles", "groups")
    @classmethod
    def normalise_subjects(cls, values: list[str]) -> list[str]:
        return _normalise_acl_values(values)


class ContentSecurity(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    origin: Literal["knowledge-retrieval"] = "knowledge-retrieval"
    trust: Literal["untrusted"] = "untrusted"
    instruction_authority: Literal["none"] = Field(
        default="none",
        serialization_alias="instructionAuthority",
    )
    handling: Literal["data-only"] = "data-only"
    injection_detected: bool = Field(default=False, serialization_alias="injectionDetected")
    signals: list[str] = Field(default_factory=list)


class RetrievalProvenance(BaseModel):
    origin: Literal["knowledge-retrieval"] = "knowledge-retrieval"
    workspace_id: str
    source: str
    document_id: str
    chunk_id: str


class CitationProvenance(BaseModel):
    workspace_id: str
    source: str
    document_id: str
    chunk_id: str
    source_uri: str | None = Field(default=None, max_length=2048)
    source_version: str | None = Field(default=None, max_length=512)
    retrieved_at: datetime
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class DocumentIn(StrictModel):
    workspace_id: str = Field(min_length=1, max_length=128, pattern=r"^[a-zA-Z0-9_.:-]+$")
    source: SourceType | str
    external_id: str | None = Field(default=None, max_length=512)
    title: str = Field(min_length=1, max_length=512)
    body: str = Field(min_length=1, max_length=2_000_000)
    metadata: dict[str, Any] = Field(default_factory=dict)
    access: AccessPolicy = Field(default_factory=AccessPolicy)
    retention_days: int | None = Field(default=None, ge=1, le=3650)

    @field_validator("source")
    @classmethod
    def validate_source(cls, value: SourceType | str) -> SourceType | str:
        source = str(value)
        if len(source) > 64 or not source.replace("-", "_").replace("_", "").isalnum():
            raise ValueError("source must be a short alphanumeric connector name")
        return value


class Document(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    workspace_id: str
    source: str
    external_id: str | None = None
    title: str
    body: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    access: AccessPolicy = Field(default_factory=AccessPolicy, exclude=True)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    retention_until: datetime | None = None
    deleted_at: datetime | None = None
    purge_after: datetime | None = None
    deletion_reason: str | None = None


class Chunk(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    document_id: str
    workspace_id: str
    text: str
    ordinal: int
    metadata: dict[str, Any] = Field(default_factory=dict)
    access: AccessPolicy = Field(default_factory=AccessPolicy, exclude=True)


class Entity(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    workspace_id: str
    document_id: str | None = None
    name: str
    type: EntityType | str = EntityType.CONCEPT
    metadata: dict[str, Any] = Field(default_factory=dict)
    access: AccessPolicy = Field(default_factory=AccessPolicy, exclude=True)


class Relationship(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    workspace_id: str
    document_id: str | None = None
    subject: str
    predicate: str
    object: str
    evidence_chunk_id: str | None = None
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    metadata: dict[str, Any] = Field(default_factory=dict)
    access: AccessPolicy = Field(default_factory=AccessPolicy, exclude=True)


class SearchRequest(StrictModel):
    workspace_id: str = Field(min_length=1, max_length=128, pattern=r"^[a-zA-Z0-9_.:-]+$")
    query: str = Field(min_length=1, max_length=8_000)
    limit: int = Field(default=10, ge=1, le=50)
    include_graph: bool = True


class SearchHit(BaseModel):
    document_id: str
    chunk_id: str
    title: str
    text: str
    score: float
    source: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    provenance: RetrievalProvenance | None = None
    citation: CitationProvenance | None = None
    security: ContentSecurity | None = None


class SearchResponse(BaseModel):
    query: str
    hits: list[SearchHit]
    related_entities: list[Entity] = Field(default_factory=list)
    relationships: list[Relationship] = Field(default_factory=list)
    security: ContentSecurity | None = None


class TombstoneDocumentRequest(StrictModel):
    workspace_id: str = Field(min_length=1, max_length=128, pattern=r"^[a-zA-Z0-9_.:-]+$")
    reason: str = Field(min_length=1, max_length=1000)


class TombstoneDocumentResponse(BaseModel):
    document_id: str
    workspace_id: str
    deleted_at: datetime
    purge_after: datetime
    reason: str


class RetentionRunRequest(StrictModel):
    workspace_id: str = Field(min_length=1, max_length=128, pattern=r"^[a-zA-Z0-9_.:-]+$")
    as_of: datetime | None = None


class RetentionRunResponse(BaseModel):
    workspace_id: str
    as_of: datetime
    tombstoned: int
    purged: int
