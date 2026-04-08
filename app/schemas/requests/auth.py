from pydantic import BaseModel, EmailStr, Field
from typing import Optional
class AuthRequest(BaseModel):
    username: str = Field(..., min_length=3, max_length=50)
    password: str = Field(..., min_length=6, max_length=128)

class LoginRequest(AuthRequest):
    name_tenant: str = Field(..., min_length=1, max_length=100, description="Tên tenant để đăng nhập") 

class RegisterRequest(AuthRequest):
    email: Optional[EmailStr] = None
    full_name: Optional[str] = Field(None, min_length=3, max_length=50)
    role_id: Optional[int] = Field(None, gt=0)
    level_id: Optional[int] = Field(None, gt=0)
    
    
    