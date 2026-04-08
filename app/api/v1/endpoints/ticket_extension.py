from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.config.database import get_db
from app.schemas.requests.ticket_extension import (
    TicketExtensionCreate,
    TicketExtensionUpdate,
    TicketExtensionResponse
)
from app.db.models import User
from app.core.security.permissions import has_permission
from app.core.config.logging import log_user_action
from app.core.dependencies.dependencies import get_current_user_dependency
from app.services.v1 import handle_ticket_extension
from uuid import UUID

router = APIRouter(
    prefix="/ticket-extensions",
    tags=["Ticket Extensions"]
)

@router.get("/{ticket_id}")
async def get_ticket_extension(
    request: Request,
    ticket_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user_dependency),
    _ = Depends(has_permission("view_ticket_extensions"))
):
    """
    Lấy extension data của một ticket
    
    - **ticket_id**: ID của ticket
    
    **Extension data** là dữ liệu mở rộng linh hoạt, lưu các custom fields động:
    - Custom form fields
    - Integration data
    - Dynamic attributes
    - Extra metadata không có trong schema cố định
    
    **Ví dụ extension data:**
    ```json
    {
      "custom_priority": "urgent",
      "external_ticket_id": "EXT-12345",
      "customer_satisfaction_score": 9,
      "tags": ["vip", "urgent"],
      "custom_fields": {
        "department": "IT",
        "category": "Hardware"
      }
    }
    ```
    """
    return await handle_ticket_extension.get_ticket_extension(ticket_id, db, current_user)


@router.post("")
@log_user_action("upsert_ticket_extension")
async def upsert_ticket_extension(
    extension_data: TicketExtensionCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user_dependency),
    _ = Depends(has_permission("create_ticket_extension")),
):
    """
    Tạo hoặc cập nhật (replace) extension data cho ticket
    
    - **ticket_id**: ID của ticket (bắt buộc)
    - **data**: Extension data dạng JSON object (tùy chọn)
    
    **Lưu ý:**
    - Nếu extension chưa tồn tại: Tạo mới (status 201)
    - Nếu extension đã tồn tại: **REPLACE** toàn bộ data (status 200)
    - Sử dụng PATCH nếu muốn merge/update từng phần
    
    **Ví dụ request body:**
    ```json
    {
      "ticket_id": "019b8bea-...",
      "data": {
        "custom_priority": "urgent",
        "external_ticket_id": "EXT-12345",
        "custom_fields": {
          "department": "IT"
        }
      }
    }
    ```
    """
    return await handle_ticket_extension.upsert_ticket_extension(extension_data, db, current_user)


@router.patch("/{ticket_id}")
@log_user_action("update_ticket_extension")
async def update_ticket_extension(
    ticket_id: UUID,
    extension_data: TicketExtensionUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user_dependency),
    _ = Depends(has_permission("edit_ticket_extension")),
):
    """
    Cập nhật (merge) extension data cho ticket
    
    - **ticket_id**: ID của ticket
    - **data**: Extension data cần merge vào data hiện tại
    
    **Lưu ý:**
    - Endpoint này **MERGE** data mới với data hiện có
    - Chỉ các field được cung cấp sẽ được update/thêm mới
    - Các field không được đề cập sẽ giữ nguyên
    
    **Ví dụ:**
    
    Data hiện tại:
    ```json
    {
      "priority": "high",
      "department": "IT",
      "tags": ["vip"]
    }
    ```
    
    Request PATCH:
    ```json
    {
      "data": {
        "priority": "urgent",
        "assigned_team": "Network"
      }
    }
    ```
    
    Kết quả sau merge:
    ```json
    {
      "priority": "urgent",        // Updated
      "department": "IT",           // Unchanged
      "tags": ["vip"],             // Unchanged
      "assigned_team": "Network"   // Added
    }
    ```
    """
    return await handle_ticket_extension.update_ticket_extension(ticket_id, extension_data, db, current_user)


@router.delete("/{ticket_id}")
@log_user_action("delete_ticket_extension")
async def delete_ticket_extension(
    ticket_id: UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user_dependency),
    _ = Depends(has_permission("delete_ticket_extension")),
):
    """
    Xóa extension data của ticket
    
    - **ticket_id**: ID của ticket
    
    Lưu ý: Xóa vĩnh viễn extension data, không thể khôi phục
    """
    return await handle_ticket_extension.delete_ticket_extension(ticket_id, db, current_user)
