from pydantic import BaseModel, EmailStr, StringConstraints
from typing import Annotated
from uuid import UUID
from typing import Optional


class CreateUserRequest(BaseModel):
    username: Annotated[str, StringConstraints(min_length=3, max_length=50)]
    email: EmailStr
    password: Annotated[str, StringConstraints(min_length=6)]
    fullname: str | None = None
    chat_id: int | None = None
    role_id: Optional[UUID] | None = None
    level_id: Optional[UUID] | None = None
    tenant_id: Optional[UUID] | None = None

class UpdateUserRequest(BaseModel):
    username: Annotated[str, StringConstraints(min_length=3, max_length=50)] | None = None
    email: EmailStr | None = None
    password: Annotated[str, StringConstraints(min_length=6)] | None = None
    fullname: str | None = None
    chat_id: int | None = None
    role_id: Optional[UUID] | None = None
    level_id: Optional[UUID] | None = None
    is_active: int | None = None
    tenant_id: Optional[UUID] | None = None

class ResponseUser(BaseModel):
    id: UUID
    username: str
    email: EmailStr
    fullname: str | None = None
    chat_id: int | None = None
    role_id: Optional[UUID] | None = None
    level_id: Optional[UUID] | None = None
    tenant_id: Optional[UUID] | None = None

