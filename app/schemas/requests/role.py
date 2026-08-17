from pydantic import BaseModel, ConfigDict
from uuid import UUID


class RoleBase(BaseModel):
    name: str
    description: str
    role_order: int


class CreateRoleRequest(RoleBase):
    tenant_id: UUID | None = None


class UpdateRoleRequest(RoleBase):
    is_active: int


class RoleResponse(BaseModel):
    id: UUID
    name: str
    description: str | None
    role_order: int
    is_active: int
    tenant_id: UUID | None = None
    model_config = ConfigDict(from_attributes=True)
