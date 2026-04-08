from pydantic import BaseModel, StringConstraints
from typing import Annotated
from typing import Optional, List
from uuid import UUID

class CreatePermissionRequest(BaseModel):
    name: Annotated[str, StringConstraints(min_length=3, max_length=50)]
    description: str | None = None

class UpdatePermissionRequest(BaseModel):
    name: Annotated[str, StringConstraints(min_length=3, max_length=50)] | None = None
    description: str | None = None
    tenant_id: Optional[UUID] | None = None

class CreatePermissionTenantRequest(BaseModel):
    tenant_id: UUID  # hoặc int nếu bạn không dùng UUID
    permissions: List[CreatePermissionRequest]

# class UpdatePermissionRequest(BaseModel):
#     tenant_id: UUID  # hoặc int nếu bạn không dùng UUID
#     permissions: List[CreatePermissionRequest]