from pydantic import BaseModel
from typing import Optional
from uuid import UUID

class GroupBase(BaseModel):
    name: str
    description: str | None = None
    department_id: UUID
    tenant_id: Optional[UUID]  | None = None
    is_active: Optional[int] | None = None

class GroupCreate(GroupBase):
    pass

class GroupUpdate(GroupBase):
    pass

class Group(GroupBase):
    id: UUID

    model_config = {
        "from_attributes": True
    }