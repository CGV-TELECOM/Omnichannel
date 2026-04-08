from pydantic import BaseModel, ConfigDict
from uuid import UUID  

class RoleBase(BaseModel):
    name: str
    description: str
    tenant_id: UUID
    role_order: int
    

class CreateRoleRequest(RoleBase):
    pass

class UpdateRoleRequest(RoleBase):
    pass
    is_active: int

class RoleResponse(BaseModel):
    id: UUID
    name: str
    description: str | None
    tenant_id: UUID
    role_order: int
    is_active: int
    model_config = ConfigDict(from_attributes=True)