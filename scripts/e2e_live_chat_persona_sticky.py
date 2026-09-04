#!/usr/bin/env python3
"""P0 E2E: select student → Redis → conversation_created-like payload → sticky kg_agent_id.

Không bấm widget; mô phỏng identifier = setUser(oh_…).
Tuỳ chọn tạo conversation Chatwoot thật rồi đọc custom_attributes (webhook).
"""
from __future__ import annotations

import asyncio
import os
import time
from uuid import UUID

from sqlalchemy import select

from app.core.config.database import async_session_maker
from app.db.models import MessagingInboxBinding, Tenant
from app.integrations.chatwoot import client as chatwoot_client
from app.services.v1.handle_chatwoot.chatbot import (
    after_bot_assigned_setup_persona,
    assign_to_ai_bot,
    fetch_conversation_custom_attributes,
    maybe_auto_assign_ai_bot,
)
from app.services.v1.handle_live_chat_public import (
    canonicalize_client_session_id,
    resolve_preselected_persona_from_conversation,
    select_public_persona,
)
from app.services.v1.handle_messaging_inbox_binding import (
    ensure_web_widget_hmac_optional_for_anonymous,
)

STUDENT_ROW = UUID("f4af93da-0a80-47f0-9b1a-0e40fc3b5fa5")
EXPECT_KG = UUID("b10add77-0a1b-4974-9411-15ff68de61cd")
WEBSITE_TOKEN = os.getenv("E2E_WEBSITE_TOKEN", "c4RWQ2z5KgxjZ88Hmx7bvKSA")
RAW_SESSION = os.getenv("E2E_CLIENT_SESSION", f"e2e-p0-{int(time.time())}")


async def _hmac_off_all_web_widgets() -> None:
    res = await chatwoot_client.application_request(
        "GET", "/api/v1/accounts/1/inboxes"
    )
    if res.status_code != 200:
        print("WARN list inboxes", res.status_code)
        return
    n = await ensure_web_widget_hmac_optional_for_anonymous(
        messaging_account_id=1,
        inboxes_payload=res.data,
    )
    print(f"hmac_mandatory turned off on {n} web widget(s)")
    chk = await chatwoot_client.application_request(
        "GET", "/api/v1/accounts/1/inboxes/4"
    )
    hmac = (chk.data or {}).get("hmac_mandatory") if isinstance(chk.data, dict) else None
    print("inbox 4 hmac_mandatory=", hmac)


async def main() -> None:
    await _hmac_off_all_web_widgets()
    session_id = canonicalize_client_session_id(RAW_SESSION)
    assert session_id
    print("setUser identifier:", session_id)

    async with async_session_maker() as db:
        sel = await select_public_persona(
            db, WEBSITE_TOKEN, str(STUDENT_ROW), client_session_id=RAW_SESSION
        )
        data = sel.get("data") if isinstance(sel, dict) else None
        print("select persisted=", (data or {}).get("persisted"))
        if not (data or {}).get("persisted"):
            raise SystemExit("select Redis failed")

        bq = await db.execute(
            select(MessagingInboxBinding).where(
                MessagingInboxBinding.website_token == WEBSITE_TOKEN
            )
        )
        binding = bq.scalar_one()
        tenant = await db.get(Tenant, binding.tenant_id)
        assert tenant

        conv_payload = {
            "id": 0,
            "channel": "Channel::WebWidget",
            "inbox_id": int(binding.inbox_id),
            "contact": {"identifier": session_id},
            "custom_attributes": {},
        }
        pre = await resolve_preselected_persona_from_conversation(
            db,
            tenant_id=tenant.id,
            inbox_id=int(binding.inbox_id),
            conversation_payload=conv_payload,
        )
        print("preselect:", pre)
        if not pre or str(pre.get("kg_agent_id")) != str(EXPECT_KG):
            raise SystemExit(f"FAIL preselect {pre}")
        print("PASS identifier→Redis→kg_agent_id", EXPECT_KG)

        # Real Chatwoot conversation (webhook path) — best-effort
        sel2 = await select_public_persona(
            db, WEBSITE_TOKEN, str(STUDENT_ROW), client_session_id=RAW_SESSION + "w"
        )
        sid2 = canonicalize_client_session_id(RAW_SESSION + "w")
        contact_res = await chatwoot_client.application_request(
            "POST",
            f"/api/v1/accounts/{int(binding.messaging_account_id)}/contacts",
            json_body={
                "inbox_id": int(binding.inbox_id),
                "name": "OmniHub E2E P0",
                "identifier": sid2,
            },
        )
        print("create contact", contact_res.status_code)
        contact = contact_res.data if isinstance(contact_res.data, dict) else {}
        payload = contact.get("payload") if isinstance(contact.get("payload"), dict) else contact
        contact_id = payload.get("id") or (payload.get("contact") or {}).get("id")
        if contact_id:
            cres = await chatwoot_client.application_request(
                "POST",
                f"/api/v1/accounts/{int(binding.messaging_account_id)}/conversations",
                json_body={
                    "inbox_id": int(binding.inbox_id),
                    "contact_id": int(contact_id),
                    "message": {"content": "e2e p0 sticky check"},
                },
            )
            print("create conversation", cres.status_code)
            conv = cres.data if isinstance(cres.data, dict) else {}
            cid = conv.get("id") or (conv.get("payload") or {}).get("id")
            if cid:
                acct = int(binding.messaging_account_id)
                cid = int(cid)
                payload = {
                    "id": cid,
                    "channel": "Channel::WebWidget",
                    "inbox_id": int(binding.inbox_id),
                    "contact": {"identifier": sid2, "id": contact_id},
                    "custom_attributes": {},
                }
                # Ưu tiên webhook OmniHub (setUser → identifier → Redis).
                fresh = {}
                for _ in range(12):
                    await asyncio.sleep(0.5)
                    fresh = await fetch_conversation_custom_attributes(acct, cid)
                    if fresh.get("kg_agent_id"):
                        break
                if str(fresh.get("kg_agent_id") or "") != str(EXPECT_KG):
                    # Application API gán sẵn agent (already_human). Widget visitor thì unassigned.
                    ok, detail = await maybe_auto_assign_ai_bot(
                        db,
                        tenant_id=tenant.id,
                        account_id=acct,
                        conversation_id=cid,
                        conversation_payload=payload,
                    )
                    print("maybe_auto_assign", ok, detail)
                    if "already_human" in str(detail):
                        aok, adetail = await assign_to_ai_bot(
                            db, tenant, acct, cid, sync_flags=True, send_note=False
                        )
                        print("assign_to_ai_bot", aok, adetail)
                        if aok:
                            pdetail = await after_bot_assigned_setup_persona(
                                db,
                                tenant=tenant,
                                account_id=acct,
                                conversation_id=cid,
                                conversation_payload=payload,
                            )
                            print("after_bot_assigned_setup_persona", pdetail)
                    fresh = await fetch_conversation_custom_attributes(acct, cid)
                print("Chatwoot conv", cid, "attrs", fresh)
                if str(fresh.get("kg_agent_id") or "") != str(EXPECT_KG):
                    raise SystemExit(
                        f"FAIL Chatwoot sticky conv={cid} attrs={fresh}"
                    )

    print("PASS P0 sticky + hmac path")


if __name__ == "__main__":
    asyncio.run(main())
