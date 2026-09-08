from __future__ import annotations

import secrets
import time
from collections.abc import Awaitable, Callable
from typing import Annotated

from fastapi import Depends, FastAPI, Header, HTTPException, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import ORJSONResponse

from universal_kg.access import AccessDeniedError, ensure_workspace_access
from universal_kg.config import Settings, get_settings
from universal_kg.domain import AccessContext, Document, DocumentIn, SearchRequest, SearchResponse
from universal_kg.logging import configure_logging, get_logger
from universal_kg.security import audit_event, client_key, rate_limiter
from universal_kg.services.ingestion import IngestionService
from universal_kg.services.search import SearchService
from universal_kg.storage.factory import get_knowledge_store

settings = get_settings()
configure_logging(settings.log_level)
logger = get_logger(__name__)

app = FastAPI(
    title="Universal AI Knowledge Graph",
    version="0.1.0",
    default_response_class=ORJSONResponse,
    description="Enterprise semantic knowledge graph and AI-search API.",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://localhost:8000"],
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=[
        "Authorization",
        "Content-Type",
        "X-API-Key",
        "X-Workspace-ID",
        "X-Actor-ID",
        "X-Actor-Roles",
        "X-Actor-Groups",
    ],
)

SettingsDependency = Annotated[Settings, Depends(get_settings)]
ApiKeyHeader = Annotated[str | None, Header(alias="X-API-Key")]
WorkspaceHeader = Annotated[str | None, Header(alias="X-Workspace-ID")]
ActorHeader = Annotated[str | None, Header(alias="X-Actor-ID")]
ActorRolesHeader = Annotated[str | None, Header(alias="X-Actor-Roles")]
ActorGroupsHeader = Annotated[str | None, Header(alias="X-Actor-Groups")]


def _split_header_values(value: str | None) -> list[str]:
    if not value:
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


async def require_api_key(
    app_settings: SettingsDependency,
    x_api_key: ApiKeyHeader = None,
) -> None:
    if app_settings.api_key:
        if not x_api_key or not secrets.compare_digest(x_api_key, app_settings.api_key):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid API key")


async def delegated_access_context(
    x_workspace_id: WorkspaceHeader = None,
    x_actor_id: ActorHeader = None,
    x_actor_roles: ActorRolesHeader = None,
    x_actor_groups: ActorGroupsHeader = None,
) -> AccessContext:
    if not x_workspace_id or not x_actor_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Delegated workspace and actor context are required",
        )
    return AccessContext(
        workspace_id=x_workspace_id,
        principal_id=x_actor_id,
        roles=_split_header_values(x_actor_roles),
        groups=_split_header_values(x_actor_groups),
    )


DelegatedAccess = Annotated[AccessContext, Depends(delegated_access_context)]


@app.middleware("http")
async def security_middleware(
    request: Request,
    call_next: Callable[[Request], Awaitable[Response]],
) -> Response:
    start = time.perf_counter()
    key = client_key(request)
    rate_limiter.check(key)
    try:
        response = await call_next(request)
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("unhandled_request_error", path=request.url.path, error=str(exc))
        return ORJSONResponse(status_code=500, content={"detail": "Internal server error"})

    response.headers["X-Process-Time"] = str(round(time.perf_counter() - start, 6))
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


@app.get("/health")
async def health(app_settings: SettingsDependency) -> dict[str, str]:
    return {
        "status": "ok",
        "service": app_settings.app_name,
        "environment": app_settings.environment,
    }


@app.get("/ready")
async def ready() -> dict[str, str]:
    try:
        await get_knowledge_store().check_ready()
    except Exception as exc:
        logger.warning("knowledge_store_not_ready", error=str(exc))
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Knowledge store is not ready",
        ) from exc
    return {"status": "ready"}


@app.get("/metrics")
async def metrics() -> dict[str, int]:
    return {"process_up": 1}


@app.post("/v1/ingest", response_model=Document, dependencies=[Depends(require_api_key)])
async def ingest(payload: DocumentIn, access: DelegatedAccess) -> Document:
    try:
        ensure_workspace_access(payload.workspace_id, access)
    except AccessDeniedError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    audit_event(
        "document.ingest",
        workspace_id=payload.workspace_id,
        metadata={
            "source": str(payload.source),
            "actor_id": access.principal_id,
            "access_visibility": payload.access.visibility,
            "source_acl_ref": payload.access.source_acl_ref,
        },
    )
    return await IngestionService().ingest(payload)


@app.post("/v1/search", response_model=SearchResponse, dependencies=[Depends(require_api_key)])
async def search(payload: SearchRequest, access: DelegatedAccess) -> SearchResponse:
    audit_event(
        "search.query",
        workspace_id=payload.workspace_id,
        metadata={
            "limit": payload.limit,
            "actor_id": access.principal_id,
            "role_count": len(access.roles),
            "group_count": len(access.groups),
        },
    )
    try:
        return await SearchService().search(payload, access)
    except AccessDeniedError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
