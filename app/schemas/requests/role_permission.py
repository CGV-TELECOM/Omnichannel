from pydantic import BaseModel
from typing import List, Optional
from uuid import UUID

class AssignPermissionsRequest(BaseModel):
    permission_ids: List[UUID]
    tenant_id: Optional[UUID] = None  # Chỉ truyền nếu có max level

class RemovePermissionRequest(BaseModel):
    permission_id: UUID
    tenant_id: Optional[UUID] = None  # Chỉ truyền nếu có max level
