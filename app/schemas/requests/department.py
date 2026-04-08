from pydantic import BaseModel
from typing import Optional
from uuid import UUID

class DepartmentBase(BaseModel):
    name: str
    description: str | None = None
    tenant_id: Optional[UUID] | None = None
    is_active: Optional[int] = 1

class DepartmentCreate(DepartmentBase):
    pass  # Không có id

class DepartmentUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    tenant_id: Optional[UUID] | None = None
    is_active: int | None = None


class Department(DepartmentBase):
    id: UUID
    model_config = {
        "from_attributes": True
    }
