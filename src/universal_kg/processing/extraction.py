from __future__ import annotations

import re
from collections import Counter

from universal_kg.domain import Chunk, Entity, Relationship

_CAPITALISED = re.compile(r"\b[A-Z][A-Za-z0-9&._-]*(?:\s+[A-Z][A-Za-z0-9&._-]*){0,4}\b")


def extract_entities(chunks: list[Chunk]) -> list[Entity]:
    counts: Counter[str] = Counter()
    workspace_id = chunks[0].workspace_id if chunks else "default"
    access_control = chunks[0].access_control.model_copy(deep=True) if chunks else None
    for chunk in chunks:
        for match in _CAPITALISED.findall(chunk.text):
            value = match.strip()
            if len(value) > 2 and value.lower() not in {"the", "this", "that"}:
                counts[value] += 1

    return [
        Entity(
            workspace_id=workspace_id,
            name=name,
            type="concept",
            metadata={"mentions": count},
            **({"access_control": access_control.model_copy(deep=True)} if access_control else {}),
        )
        for name, count in counts.most_common(50)
    ]


def extract_relationships(chunks: list[Chunk], entities: list[Entity]) -> list[Relationship]:
    relationships: list[Relationship] = []
    names = [entity.name for entity in entities]
    for chunk in chunks:
        present = [name for name in names if name in chunk.text]
        for left, right in zip(present, present[1:], strict=False):
            relationships.append(
                Relationship(
                    workspace_id=chunk.workspace_id,
                    subject=left,
                    predicate="co_occurs_with",
                    object=right,
                    evidence_chunk_id=chunk.id,
                    confidence=0.55,
                    metadata={"extractor": "rule-based-v1"},
                    access_control=chunk.access_control.model_copy(deep=True),
                )
            )
    return relationships
