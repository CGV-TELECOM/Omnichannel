from pydantic import BaseModel
from typing import List
from uuid import UUID


class AssignPermissionsRequest(BaseModel):
    permission_ids: List[UUID]


class RemovePermissionRequest(BaseModel):
    permission_id: UUID
