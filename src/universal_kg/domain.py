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


class AccessControl(StrictModel):
    visibility: Literal["workspace", "restricted"] = "workspace"
    allowed_principals: list[str] = Field(default_factory=list, max_length=256)
    source_acl_version: str | None = Field(default=None, max_length=512)

    @field_validator("allowed_principals")
    @classmethod
    def validate_principals(cls, values: list[str]) -> list[str]:
        normalized: list[str] = []
        seen: set[str] = set()
        for raw in values:
            value = raw.strip()
            if not value or len(value) > 256:
                raise ValueError("principal IDs must be non-empty and at most 256 characters")
            if value not in seen:
                normalized.append(value)
                seen.add(value)
        return normalized

    @model_validator(mode="after")
    def validate_visibility(self) -> AccessControl:
        if self.visibility == "workspace" and self.allowed_principals:
            raise ValueError("workspace-visible content must not declare allowed_principals")
        return self

    def allows(self, principal_ids: frozenset[str]) -> bool:
        if self.visibility == "workspace":
            return True
        return bool(principal_ids.intersection(self.allowed_principals))


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
    access_control: AccessControl = Field(default_factory=AccessControl)

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
    access_control: AccessControl = Field(default_factory=AccessControl)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class Chunk(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    document_id: str
    workspace_id: str
    text: str
    ordinal: int
    metadata: dict[str, Any] = Field(default_factory=dict)
    access_control: AccessControl = Field(default_factory=AccessControl)


class Entity(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    workspace_id: str
    name: str
    type: EntityType | str = EntityType.CONCEPT
    metadata: dict[str, Any] = Field(default_factory=dict)
    access_control: AccessControl = Field(default_factory=AccessControl)


class Relationship(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    workspace_id: str
    subject: str
    predicate: str
    object: str
    evidence_chunk_id: str | None = None
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    metadata: dict[str, Any] = Field(default_factory=dict)
    access_control: AccessControl = Field(default_factory=AccessControl)


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
