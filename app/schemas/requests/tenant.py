from pydantic import BaseModel
from typing import Optional
from uuid import UUID  

class GroupBase(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None

class TenantCreate(GroupBase):
    pass

class TenantUpdate(GroupBase):
    pass

class TenantResponse(GroupBase):
    id: Optional[UUID] = None  # sửa lại kiểu đúng
    is_active: Optional[int] = None

    class Config:
        from_attributes = True  # để serialize từ ORM
