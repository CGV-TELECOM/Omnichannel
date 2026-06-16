from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config.database import get_db
from app.services.v1 import handle_chatwoot

router = APIRouter(prefix="/chatwoot-webhooks", tags=["Chatwoot Webhook"])


@router.post("")
async def receive_chatwoot_webhook(
    payload: dict,
    db: AsyncSession = Depends(get_db)
):
    """
    Endpoint tiếp nhận webhook từ Chatwoot (không cần Token JWT).
    Sau khi nhận, thông tin được xử lý và truyền qua Socket.IO.
    """
    event = payload.get("event", "unknown")
    print(f"[CHATWOOT WEBHOOK] Nhận sự kiện: {event}")
    return await handle_chatwoot.handle_webhook(payload, db)
