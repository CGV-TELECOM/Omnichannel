from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi import Request
from app.core.config.database import get_db
from app.core.config.logging import log_user_action
from app.core.dependencies.dependencies import get_current_user_dependency
from app.core.security.permissions import has_permission
from app.db.models import User
from app.schemas.requests.conversation_rating import (
    ConversationRatingSendBody,
    ConversationRatingSubmitBody,
)
from app.services.v1 import handle_conversation_rating

# Public (không JWT) — mount riêng trong main.py
public_router = APIRouter(prefix="/ratings", tags=["Conversation Ratings"])

# Protected
router = APIRouter(prefix="/conversation-ratings", tags=["Conversation Ratings"])


@public_router.get("/{token}")
async def get_public_rating(
    token: str,
    db: AsyncSession = Depends(get_db),
):
    """Khách mở link đánh giá — lấy trạng thái form."""
    return await handle_conversation_rating.get_rating_by_token(token, db)


@public_router.post("/{token}")
async def submit_public_rating(
    token: str,
    body: ConversationRatingSubmitBody,
    db: AsyncSession = Depends(get_db),
):
    """Khách gửi điểm 1–5 (+ comment)."""
    return await handle_conversation_rating.submit_rating(
        token=token,
        score=body.score,
        comment=body.comment,
        db=db,
    )


@router.get("/tenants/{tenant_id}")
async def list_tenant_ratings(
    tenant_id: UUID,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    status: str | None = Query(None, description="pending | submitted | expired"),
    channel: str | None = Query(None),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user_dependency),
    _=Depends(has_permission("view_messaging_conversations")),
):
    """Danh sách CSAT OmniHub theo tenant."""
    return await handle_conversation_rating.list_ratings(
        db=db,
        current_user=current_user,
        tenant_id=tenant_id,
        page=page,
        page_size=page_size,
        status=status,
        channel=channel,
    )


@router.post("/tenants/{tenant_id}/conversations/{conversation_id}/send")
@log_user_action("sendConversationRatingLink")
async def send_conversation_rating_link(
    request: Request,
    tenant_id: UUID,
    conversation_id: int,
    body: ConversationRatingSendBody | None = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user_dependency),
    _=Depends(has_permission("view_messaging_conversations")),
):
    """
    Chủ động gửi link CSAT — chỉ nhân viên đang được gán conversation
    (hoặc platform admin). Cần quyền xem conversation.
    """
    payload = body or ConversationRatingSendBody()
    return await handle_conversation_rating.send_rating_manually(
        db=db,
        current_user=current_user,
        tenant_id=tenant_id,
        conversation_id=conversation_id,
        force_resend=payload.force_resend,
    )
