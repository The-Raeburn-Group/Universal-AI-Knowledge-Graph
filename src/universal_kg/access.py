from __future__ import annotations

from universal_kg.domain import AccessContext, AccessPolicy


class AccessDeniedError(PermissionError):
    pass


def access_allows(policy: AccessPolicy, context: AccessContext) -> bool:
    if policy.visibility == "workspace":
        return True

    if context.principal_id in policy.principals:
        return True
    if set(context.roles).intersection(policy.roles):
        return True
    return bool(set(context.groups).intersection(policy.groups))


def ensure_workspace_access(workspace_id: str, context: AccessContext) -> None:
    if workspace_id != context.workspace_id:
        raise AccessDeniedError("delegated workspace does not match requested workspace")
