from pydantic import BaseModel, Field, validator
from typing import Optional
from uuid import UUID
import re


class TagCreate(BaseModel):
    """Schema cho việc tạo Tag mới"""

    name: str = Field(..., min_length=1, max_length=100, description="Tên tag")
    description: Optional[str] = Field(None, max_length=500, description="Mô tả tag")
    color: Optional[str] = Field(None, description="Mã màu hex (ví dụ: #FF5733)")
    type: str = Field(
        default="ticket",
        description="Loại tag: 'ticket' hoặc 'customer' (mặc định: ticket)",
    )

    @validator("name")
    def validate_name(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("Tên tag không được để trống")
        return v.strip()

    @validator("color")
    def validate_color(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        v = v.strip()
        if not re.match(r"^#[0-9A-Fa-f]{6}$", v):
            raise ValueError(
                "Mã màu phải theo định dạng hex 6 chữ số (ví dụ: #FF5733)"
            )
        return v.upper()

    @validator("type")
    def validate_type(cls, v: str) -> str:
        v = v.strip().lower()
        if v not in {"ticket", "customer"}:
            raise ValueError("type phải là 'ticket' hoặc 'customer'")
        return v

    class Config:
        json_schema_extra = {
            "example": {
                "name": "Khẩn cấp",
                "description": "Tag cho các ticket khẩn cấp",
                "color": "#FF5733",
                "type": "ticket",
            }
        }


class TagUpdate(BaseModel):
    """Schema cho việc cập nhật Tag"""

    name: Optional[str] = Field(
        None, min_length=1, max_length=100, description="Tên tag"
    )
    description: Optional[str] = Field(
        None, max_length=500, description="Mô tả tag"
    )
    color: Optional[str] = Field(
        None, description="Mã màu hex (ví dụ: #FF5733)"
    )
    is_active: Optional[int] = Field(
        None, ge=0, le=1, description="Trạng thái active (0/1)"
    )
    type: Optional[str] = Field(
        None,
        description="Loại tag: 'ticket' hoặc 'customer' (nếu muốn thay đổi)",
    )

    @validator("name")
    def validate_name(cls, v: Optional[str]) -> Optional[str]:
        if v is not None:
            if not v.strip():
                raise ValueError("Tên tag không được để trống")
            return v.strip()
        return v

    @validator("color")
    def validate_color(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        v = v.strip()
        if not re.match(r"^#[0-9A-Fa-f]{6}$", v):
            raise ValueError(
                "Mã màu phải theo định dạng hex 6 chữ số (ví dụ: #FF5733)"
            )
        return v.upper()

    @validator("type")
    def validate_type(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        v = v.strip().lower()
        if v not in {"ticket", "customer"}:
            raise ValueError("type phải là 'ticket' hoặc 'customer'")
        return v

    class Config:
        json_schema_extra = {
            "example": {
                "name": "Khẩn cấp",
                "description": "Tag cho các ticket khẩn cấp cần xử lý ngay",
                "color": "#FF0000",
                "is_active": 1,
                "type": "ticket",
            }
        }


class TagResponse(BaseModel):
    """Schema cho response Tag"""

    id: UUID
    name: str
    description: Optional[str] = None
    color: Optional[str] = None
    type: str
    created_at: str
    updated_at: str
    tenant_id: Optional[UUID] = None
    is_active: int

    class Config:
        from_attributes = True
        json_schema_extra = {
            "example": {
                "id": "019b8bea-d0b3-7d18-b717-228a2bab0c17",
                "name": "Khẩn cấp",
                "description": "Tag cho các ticket khẩn cấp",
                "color": "#FF5733",
                "type": "ticket",
                "created_at": "2025-01-09T02:53:00Z",
                "updated_at": "2025-01-09T02:53:00Z",
                "tenant_id": "019b8bea-cb75-72da-8cb0-66359f310427",
                "is_active": 1,
            }
        }
