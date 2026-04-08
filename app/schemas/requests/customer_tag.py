from typing import List
from uuid import UUID

from pydantic import BaseModel, Field


class CustomerTagUpdateRequest(BaseModel):
    """
    Payload dùng cho việc gán / gỡ nhiều tag cho một customer.
    """

    tag_ids: List[UUID] = Field(
        ..., min_length=1, description="Danh sách ID tag (type=CUSTOMER)"
    )

