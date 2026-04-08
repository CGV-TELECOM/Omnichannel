from pydantic import BaseModel
from typing import List

from uuid import UUID

class UserGroupCreate(BaseModel):
    user_id: UUID
    group_id: UUID

class UserGroupCreateList(BaseModel):
    items: List[UserGroupCreate]
    
class UserGroupDelete(BaseModel):
    user_id: UUID
    group_id: UUID
