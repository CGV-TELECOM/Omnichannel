from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config.database import get_db
from app.core.config.logging import log_user_action
from app.core.dependencies.dependencies import get_current_user_dependency
from app.core.redis.redis_config import RedisHelper
from app.core.security.permissions import has_permission
from app.db.models import User
from app.schemas.requests.conversation_rating import (
    ConversationRatingSendBody,
    ConversationRatingSubmitBody,
)
from app.schemas.responses.api_response_rule import (
    ResponseStatus,
    ResponseStatusCode,
    api_response,
)
from app.services.v1 import handle_conversation_rating

# Public (không JWT) — mount riêng trong main.py
public_router = APIRouter(prefix="/ratings", tags=["Conversation Ratings"])

# Protected
router = APIRouter(prefix="/conversation-ratings", tags=["Conversation Ratings"])

_PUBLIC_RATING_RATE_LIMIT = 30
_PUBLIC_RATING_RATE_WINDOW = 60


async def _enforce_public_rating_rate_limit(request: Request):
    """Giới hạn spam/probe token trên API public CSAT."""
    client = request.client.host if request.client else "unknown"
    key = f"rate:public_rating:{client}"
    try:
        n = await RedisHelper.increment(key, expire_seconds=_PUBLIC_RATING_RATE_WINDOW)
    except Exception:
        # Redis down → không chặn form khách
        return None
    if n > _PUBLIC_RATING_RATE_LIMIT:
        return JSONResponse(
            status_code=429,
            content=api_response(
                ResponseStatus.ERROR,
                ResponseStatusCode.TOMANY_REQUESTS,
                "Quá nhiều yêu cầu. Vui lòng thử lại sau.",
            ),
        )
    return None


@public_router.get("/{token}")
async def get_public_rating(
    token: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Khách mở link đánh giá — lấy trạng thái form."""
    limited = await _enforce_public_rating_rate_limit(request)
    if limited is not None:
        return limited
    return await handle_conversation_rating.get_rating_by_token(token, db)


@public_router.post("/{token}")
async def submit_public_rating(
    token: str,
    body: ConversationRatingSubmitBody,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Khách gửi điểm 1–5 (+ comment)."""
    limited = await _enforce_public_rating_rate_limit(request)
    if limited is not None:
        return limited
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
