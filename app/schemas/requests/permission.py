from pydantic import BaseModel, StringConstraints
from typing import Annotated, List, Optional
from uuid import UUID


class CreatePermissionRequest(BaseModel):
    name: Annotated[str, StringConstraints(min_length=3, max_length=50)]
    description: str | None = None


class UpdatePermissionRequest(BaseModel):
    name: Annotated[str, StringConstraints(min_length=3, max_length=50)] | None = None
    description: str | None = None


class CreatePermissionTenantRequest(BaseModel):
    """Bulk create permissions (catalog dùng chung — không còn tenant_id)."""
    permissions: List[CreatePermissionRequest]
