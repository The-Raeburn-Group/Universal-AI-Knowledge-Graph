from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from typing import Any

from pydantic import BaseModel, Field

from universal_kg.domain import DocumentIn


class ConnectorConfig(BaseModel):
    workspace_id: str
    source_name: str
    credentials: dict[str, str] = Field(default_factory=dict)
    options: dict[str, Any] = Field(default_factory=dict)


class Connector(ABC):
    def __init__(self, config: ConnectorConfig) -> None:
        self.config = config

    @abstractmethod
    def load(self) -> AsyncIterator[DocumentIn]:
        raise NotImplementedError


class ConnectorRegistry:
    def __init__(self) -> None:
        self._connectors: dict[str, type[Connector]] = {}

    def register(self, name: str, connector: type[Connector]) -> None:
        self._connectors[name] = connector

    def create(self, name: str, config: ConnectorConfig) -> Connector:
        if name not in self._connectors:
            raise KeyError(f"Unknown connector: {name}")
        return self._connectors[name](config)


registry = ConnectorRegistry()
