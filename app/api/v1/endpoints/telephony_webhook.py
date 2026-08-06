from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config.database import get_db
from app.services.v1 import handle_telephony_webhook

router = APIRouter(prefix="/telephony-webhooks", tags=["Telephony Webhook"])


@router.post("")
async def receive_telephony_webhook(
    payload: dict,
    db: AsyncSession = Depends(get_db),
):
    """
    Webhook public từ tổng đài (không JWT).
    Bắt buộc `sip_call_id` (UUID) — khóa map cuộc gọi.
    Event: ringing | answered | hangup | cdr | ...
    """
    state = payload.get("state", "unknown")
    sip = payload.get("sip_call_id")
    print(f"[TELEPHONY WEBHOOK] state={state} sip_call_id={sip}")
    return await handle_telephony_webhook.handle_telephony_webhook(db, payload)
