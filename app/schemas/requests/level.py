from pydantic import BaseModel, Field, ConfigDict
from uuid import UUID

class CreateLevelRequest(BaseModel):
    name: str = Field(..., description="Tên cấp độ")
    description: str = Field(..., description="Mô tả cấp độ")

class UpdateLevelRequest(BaseModel):
    name: str = Field(..., description="Tên cấp độ")
    description: str = Field(..., description="Mô tả cấp độ")

class LevelResponse(BaseModel):
    id: UUID
    name: str
    description: str
    level_order: int
    model_config = ConfigDict(from_attributes=True)