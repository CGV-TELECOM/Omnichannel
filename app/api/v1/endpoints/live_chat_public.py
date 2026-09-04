"""Public Live Chat — personas theo website_token (không JWT)."""

from typing import Any, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config.database import get_db
from app.core.redis.redis_config import RedisHelper
from app.schemas.responses.api_response_rule import (
    ResponseStatus,
    ResponseStatusCode,
    api_response,
)
from app.services.v1 import handle_live_chat_public

router = APIRouter(prefix="/public/live-chat", tags=["Public Live Chat"])

_RATE_LIMIT = 100
_RATE_WINDOW = 100

class LiveChatPersonaSelectBody(BaseModel):
    persona_id: UUID = Field(
        ...,
        description="Opaque id từ GET .../personas → personas[].id",
    )
    client_session_id: str = Field(
        ...,
        min_length=8,
        max_length=128,
        description=(
            "ID phiên do FE sinh. Backend canonicalize (prefix oh_). "
            "Dùng data.client_session_id trả về cho $chatwoot.setUser."
        ),
    )
    meta: Optional[dict[str, Any]] = Field(
        default=None,
        description="Meta mở rộng tùy chọn (campaign, locale, …) — lưu Redis / sticky",
    )


async def _rate_limit(request: Request):
    client = request.client.host if request.client else "unknown"
    key = f"rate:public_live_chat:{client}"
    try:
        n = await RedisHelper.increment(key, expire_seconds=_RATE_WINDOW)
    except Exception:
        # Redis rate-limit down → fail-open (không chặn visitor)
        return None
    if n > _RATE_LIMIT:
        return JSONResponse(
            status_code=429,
            content=api_response(
                ResponseStatus.ERROR,
                ResponseStatusCode.TOMANY_REQUESTS,
                "Quá nhiều yêu cầu. Vui lòng thử lại sau.",
            ),
        )
    return None


@router.get("/{website_token}/personas")
async def get_live_chat_personas(
    website_token: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    limited = await _rate_limit(request)
    if limited is not None:
        return limited
    return await handle_live_chat_public.get_public_personas_by_website_token(
        db, website_token
    )


@router.post("/{website_token}/personas/select")
async def select_live_chat_persona(
    website_token: str,
    body: LiveChatPersonaSelectBody,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> Any:
    """
    Chọn persona trước khi inject Chatwoot.
    Redis down → vẫn 200 với persisted=false (fallback menu trong chat).
    """
    limited = await _rate_limit(request)
    if limited is not None:
        return limited
    xff = request.headers.get("x-forwarded-for")
    client_ip = (
        xff.split(",")[0].strip()
        if xff
        else (request.client.host if request.client else None)
    )
    return await handle_live_chat_public.select_public_persona(
        db,
        website_token,
        str(body.persona_id),
        client_session_id=body.client_session_id,
        meta=body.meta,
        client_ip=client_ip,
    )
