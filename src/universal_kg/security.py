from __future__ import annotations

import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Any

from fastapi import HTTPException, Request, status

from universal_kg.logging import get_logger

logger = get_logger(__name__)


@dataclass
class InMemoryRateLimiter:
    max_requests: int = 120
    window_seconds: int = 60
    _requests: dict[str, deque[float]] = field(default_factory=lambda: defaultdict(deque))

    def check(self, key: str) -> None:
        now = time.time()
        bucket = self._requests[key]
        while bucket and bucket[0] <= now - self.window_seconds:
            bucket.popleft()
        if len(bucket) >= self.max_requests:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Rate limit exceeded",
            )
        bucket.append(now)


rate_limiter = InMemoryRateLimiter()


def client_key(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def audit_event(
    action: str,
    workspace_id: str | None,
    metadata: dict[str, Any] | None = None,
) -> None:
    logger.info(
        "audit_event",
        action=action,
        workspace_id=workspace_id,
        metadata=metadata or {},
    )


def require_human_approval(approved: bool, action: str) -> None:
    if not approved:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Human approval required for {action}",
        )
