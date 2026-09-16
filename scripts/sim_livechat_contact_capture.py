#!/usr/bin/env python3
"""Giả lập / regression test livechat contact capture (không cần JWT đầy đủ).

Chạy:
  cd /root/backend-onmihub && PYTHONPATH=. python scripts/sim_livechat_contact_capture.py
"""

from __future__ import annotations

import asyncio
import json
import sys
import traceback
from typing import Any
from uuid import uuid4

import httpx

BASE = "http://127.0.0.1:8000"
PASS = 0
FAIL = 0
ERRORS: list[str] = []


def ok(name: str, detail: str = "") -> None:
    global PASS
    PASS += 1
    print(f"  ✅ {name}" + (f" — {detail}" if detail else ""))


def bad(name: str, detail: str) -> None:
    global FAIL
    FAIL += 1
    ERRORS.append(f"{name}: {detail}")
    print(f"  ❌ {name} — {detail}")


def section(title: str) -> None:
    print(f"\n=== {title} ===")


def test_unit_helpers() -> None:
    section("1. Unit: contact_capture helpers")
    from app.services.v1.handle_chatwoot.contact_capture import (
        apply_contact_capture_to_inbox_payload,
        build_chatwoot_pre_chat_payload,
        contact_capture_from_inbox_payload,
        enrich_inbox_api_result,
        extract_contact_fields_from_custom_attributes,
        extract_contact_id_from_payload,
        normalize_contact_payload,
        parse_contact_capture_policy,
        policy_from_chatwoot_pre_chat,
        policy_to_public_dict,
    )

    # parse + build pre_chat
    policy = parse_contact_capture_policy(
        {
            "enabled": True,
            "mode": "pre_chat_or_bot",
            "message": "Cho mình xin thông tin",
            "fields": [
                {"key": "name", "enabled": True, "required": True},
                {"key": "phone", "enabled": True, "required": True},
                {"key": "email", "enabled": True, "required": False},
            ],
        }
    )
    if policy.enabled and policy.mode == "pre_chat_or_bot":
        ok("parse policy pre_chat_or_bot")
    else:
        bad("parse policy", f"enabled={policy.enabled} mode={policy.mode}")

    cw = build_chatwoot_pre_chat_payload(policy)
    if cw.get("pre_chat_form_enabled") is True:
        ok("build pre_chat enabled")
    else:
        bad("build pre_chat enabled", str(cw))
    fields = cw["pre_chat_form_options"]["pre_chat_fields"]
    names = {f["name"]: f for f in fields}
    if set(names) >= {"fullName", "phoneNumber", "emailAddress"}:
        ok("CW field names mapped")
    else:
        bad("CW field names", str(names.keys()))
    if names["fullName"]["required"] is True and names["emailAddress"]["required"] is False:
        ok("required flags")
    else:
        bad("required flags", str(names))

    bot_payload = apply_contact_capture_to_inbox_payload(
        {
            "name": "Web",
            "contact_capture": {
                "enabled": True,
                "mode": "bot",
                "fields": [{"key": "name", "enabled": True, "required": True}],
            },
        }
    )
    ch = bot_payload.get("channel") or {}
    if (
        "contact_capture" not in bot_payload
        and ch.get("pre_chat_form_enabled") is False
        and "pre_chat_form_enabled" not in bot_payload
    ):
        ok("mode=bot → channel.pre_chat disabled + pops contact_capture")
    else:
        bad("mode=bot", str(bot_payload))

    # fields under channel when enabling pre_chat
    on_payload = apply_contact_capture_to_inbox_payload(
        {
            "welcome_title": "Top",
            "channel": {"website_url": "https://x.com"},
            "contact_capture": {
                "enabled": True,
                "mode": "pre_chat",
                "message": "Hi",
                "fields": [{"key": "name", "enabled": True, "required": True}],
            },
        }
    )
    ch2 = on_payload.get("channel") or {}
    if (
        ch2.get("pre_chat_form_enabled") is True
        and "welcome_title" not in on_payload
        and ch2.get("welcome_title") == "Top"
        and "pre_chat_form_enabled" not in on_payload
    ):
        ok("pre_chat + hoist welcome_title into channel")
    else:
        bad("channel nest", str(on_payload))

    # fields list explicit — only listed keys enabled
    only_name = parse_contact_capture_policy(
        {
            "enabled": True,
            "mode": "pre_chat",
            "fields": [{"key": "name", "enabled": True, "required": True}],
        }
    )
    by = {f.key: f for f in only_name.fields}
    if by["name"].enabled and not by["phone"].enabled and not by["email"].enabled:
        ok("explicit fields list disables others")
    else:
        bad("explicit fields", str([(f.key, f.enabled) for f in only_name.fields]))

    # normalize
    cleaned, errors = normalize_contact_payload(
        {"name": "  Nguyễn A ", "email": "A@Ex.COM", "phone": "0912 345 678"},
    )
    if cleaned.get("name") == "Nguyễn A" and cleaned.get("email") == "a@ex.com" and cleaned.get("phone") == "+84912345678":
        ok("normalize trim/lower/phone → E.164")
    else:
        bad("normalize", f"{cleaned} {errors}")

    cleaned_vn, err_vn = normalize_contact_payload({"phone": "0834802680"})
    if cleaned_vn.get("phone") == "+84834802680" and not err_vn:
        ok("VN local phone → +84 E.164")
    else:
        bad("VN e164", f"{cleaned_vn} {err_vn}")

    cleaned2, errors2 = normalize_contact_payload(
        {"name": "A", "email": "bad"},
        policy=policy,
    )
    if "email_invalid" in errors2 and "phone_required" in errors2:
        ok("validate required + email_invalid")
    else:
        bad("validate", f"{cleaned2} {errors2}")

    # roundtrip from CW inbox payload
    derived = contact_capture_from_inbox_payload(
        {
            "pre_chat_form_enabled": True,
            "pre_chat_form_options": cw["pre_chat_form_options"],
            "website_token": "tok_test",
        }
    )
    if derived.get("enabled") and derived.get("mode") != "off":
        ok("derive contact_capture from inbox", derived.get("mode"))
    else:
        bad("derive from inbox", str(derived))

    # enrich API result
    result = {
        "status": "success",
        "data": {
            "messaging": {
                "id": 15,
                "pre_chat_form_enabled": True,
                "pre_chat_form_options": cw["pre_chat_form_options"],
                "website_token": "abc",
            }
        },
    }
    enriched = enrich_inbox_api_result(result)
    cap = enriched["data"]["messaging"].get("contact_capture")
    if isinstance(cap, dict) and cap.get("enabled"):
        ok("enrich_inbox_api_result")
    else:
        bad("enrich", str(enriched))

    # extract contact id
    cid = extract_contact_id_from_payload(
        {"meta": {"sender": {"id": 99, "type": "contact"}}, "id": 1}
    )
    if cid == 99:
        ok("extract_contact_id from meta.sender")
    else:
        bad("extract_contact_id", str(cid))

    attrs = extract_contact_fields_from_custom_attributes(
        {"contact_name": "B", "contact_phone": "+84911", "omnihub_contact": {"email": "b@x.com"}}
    )
    # phone +84911 may fail length validation
    if attrs.get("name") == "B" and attrs.get("email") == "b@x.com":
        ok("extract attrs name/email", str(attrs))
    else:
        # phone short may strip email merge - check
        bad("extract attrs", str(attrs))

    # mode=bot: Chatwoot không giữ meta → Redis cache là nguồn (sim cache roundtrip)
    from app.services.v1.handle_chatwoot.contact_capture import (
        cache_contact_capture_policy,
        get_cached_contact_capture_policy,
        policy_to_public_dict,
    )

    bot_policy = parse_contact_capture_policy(
        {
            "enabled": True,
            "mode": "bot",
            "fields": [
                {"key": "name", "enabled": True, "required": True},
                {"key": "phone", "enabled": True, "required": True},
            ],
        }
    )
    bot_cw = build_chatwoot_pre_chat_payload(bot_policy)
    if bot_cw.get("pre_chat_form_enabled") is False:
        ok("bot mode: pre_chat disabled on CW")
    else:
        bad("bot mode pre_chat", str(bot_cw.get("pre_chat_form_enabled")))

    # policy off default
    off = policy_to_public_dict(policy_from_chatwoot_pre_chat(enabled=False, options=None))
    if off["enabled"] is False:
        ok("policy off default")
    else:
        bad("policy off", str(off))


async def test_bot_mode_redis_roundtrip() -> None:
    section("1b. Bot mode via Redis cache (Chatwoot không giữ meta)")
    from app.services.v1.handle_chatwoot.contact_capture import (
        cache_contact_capture_policy,
        get_cached_contact_capture_policy,
        parse_contact_capture_policy,
        policy_to_public_dict,
    )

    token = f"bot_mode_{uuid4().hex[:8]}"
    public = policy_to_public_dict(
        parse_contact_capture_policy(
            {
                "enabled": True,
                "mode": "bot",
                "message": "Bot hỏi SĐT",
                "fields": [{"key": "phone", "enabled": True, "required": True}],
            }
        )
    )
    await cache_contact_capture_policy(website_token=token, policy=public)
    cached = await get_cached_contact_capture_policy(token)
    if cached and cached.get("mode") == "bot" and cached.get("enabled") is True:
        ok("bot mode survives Redis policy cache")
    else:
        bad("bot redis roundtrip", str(cached))


async def test_redis_pending() -> None:
    section("2. Redis pending contact store/consume")
    from app.services.v1.handle_chatwoot.contact_capture import (
        consume_pending_contact,
        store_pending_contact,
    )
    from uuid import UUID

    token = f"test_tok_{uuid4().hex[:8]}"
    sid = f"oh_test_{uuid4().hex[:12]}"
    tid = UUID(int=0x1234567890)
    stored = await store_pending_contact(
        website_token=token,
        client_session_id=sid,
        tenant_id=tid,
        inbox_id=15,
        contact={"name": "Test User", "email": "t@ex.com", "phone": "0912345678"},
        ttl_seconds=120,
    )
    if stored:
        ok("store_pending_contact")
    else:
        bad("store_pending_contact", "Redis set failed — Redis down?")
        return

    pending = await consume_pending_contact(
        website_token=token,
        client_session_ids=[sid],
        expected_tenant_id=tid,
    )
    if pending and pending.get("contact", {}).get("name") == "Test User":
        ok("consume_pending_contact once", json.dumps(pending.get("contact")))
    else:
        bad("consume", str(pending))

    again = await consume_pending_contact(
        website_token=token,
        client_session_ids=[sid],
        expected_tenant_id=tid,
    )
    if again is None:
        ok("consume is one-shot (deleted)")
    else:
        bad("consume one-shot", str(again))

    # cache policy
    from app.services.v1.handle_chatwoot.contact_capture import (
        cache_contact_capture_policy,
        get_cached_contact_capture_policy,
    )

    await cache_contact_capture_policy(
        website_token=token,
        policy={"enabled": True, "mode": "pre_chat", "message": "hi", "fields": []},
    )
    cached = await get_cached_contact_capture_policy(token)
    if cached and cached.get("mode") == "pre_chat":
        ok("cache contact policy")
    else:
        bad("cache policy", str(cached))


async def test_webhook_apply_logic() -> None:
    section("3. Webhook apply logic (mock payload, no Chatwoot PATCH assert)")
    from app.services.v1.handle_chatwoot.contact_capture import (
        extract_contact_id_from_payload,
        extract_website_token_from_payload,
        normalize_contact_payload,
        store_pending_contact,
        consume_pending_contact,
    )
    from uuid import UUID

    token = f"wh_tok_{uuid4().hex[:8]}"
    sid = f"oh_wh_{uuid4().hex[:12]}"
    tid = UUID(int=0xABCDEF)
    await store_pending_contact(
        website_token=token,
        client_session_id=sid,
        tenant_id=tid,
        inbox_id=99,
        contact={"name": "Webhook User", "phone": "0987654321"},
    )

    conv = {
        "id": 501,
        "inbox_id": 99,
        "meta": {"sender": {"id": 777, "type": "contact", "identifier": sid, "name": "Khách truy cập"}},
        "contact": {"id": 777, "identifier": sid},
        "inbox": {"id": 99, "website_token": token},
    }
    cid = extract_contact_id_from_payload(conv)
    wtok = extract_website_token_from_payload(conv)
    if cid == 777 and wtok == token:
        ok("webhook payload extract contact_id + website_token")
    else:
        bad("webhook extract", f"cid={cid} tok={wtok}")

    # Simulate consume path used by webhook
    pending = await consume_pending_contact(
        website_token=wtok,
        client_session_ids=[sid],
        expected_tenant_id=tid,
    )
    cleaned, errors = normalize_contact_payload(pending.get("contact") if pending else {})
    if pending and not errors and cleaned.get("name") == "Webhook User":
        ok("webhook pending→normalize ready to PATCH", str(cleaned))
    else:
        bad("webhook pending path", f"{pending} {errors}")


async def test_http_routes() -> None:
    section("4. HTTP: OpenAPI + public endpoints")
    async with httpx.AsyncClient(base_url=BASE, timeout=30.0) as client:
        try:
            r = await client.get("/openapi.json")
        except Exception as e:
            bad("server reachable", str(e))
            return
        if r.status_code != 200:
            bad("openapi", f"status={r.status_code}")
            return
        ok("openapi 200")
        paths = r.json().get("paths") or {}
        need = [
            "/api/v1/public/live-chat/{website_token}/contact",
            "/api/v1/messaging/tenants/{tenant_id}/contacts/upsert",
        ]
        for p in need:
            if p in paths:
                ok(f"route registered {p}")
            else:
                # try fuzzy
                hits = [k for k in paths if "contact" in k]
                bad(f"route {p}", f"missing; contact-ish={hits[:8]}")

        # Bad token personas
        r2 = await client.get("/api/v1/public/live-chat/__no_such_token__/personas")
        body2 = r2.json() if r2.headers.get("content-type", "").startswith("application/json") else {}
        if r2.status_code in (200, 404) and (
            body2.get("status") == "error" or body2.get("status_code") in (404, 400)
        ):
            ok("personas unknown token → error", f"http={r2.status_code} status={body2.get('status')}")
        else:
            bad("personas unknown token", f"http={r2.status_code} body={body2}")

        # Contact without valid binding
        r3 = await client.post(
            "/api/v1/public/live-chat/__no_such_token__/contact",
            json={
                "client_session_id": "oh_testsession01",
                "name": "A",
                "phone": "0912345678",
            },
        )
        body3 = r3.json() if r3.content else {}
        if body3.get("status") == "error":
            ok("public contact unknown token → error", body3.get("message", "")[:80])
        else:
            bad("public contact unknown", f"http={r3.status_code} {body3}")

        # Validation: short session
        r4 = await client.post(
            "/api/v1/public/live-chat/__no_such_token__/contact",
            json={"client_session_id": "short", "name": "A"},
        )
        # FastAPI 422 validation
        if r4.status_code == 422:
            ok("public contact validates client_session_id min length")
        else:
            body4 = r4.json() if r4.content else {}
            # might be 400 from our handler if pydantic bypassed
            if body4.get("status") == "error":
                ok("public contact rejects short session (app error)")
            else:
                bad("short session", f"http={r4.status_code} {body4}")

        # Upsert without JWT
        r5 = await client.post(
            f"/api/v1/messaging/tenants/{uuid4()}/contacts/upsert",
            json={"conversation_id": 1, "name": "X"},
        )
        if r5.status_code in (401, 403):
            ok("upsert requires auth", f"http={r5.status_code}")
        else:
            bad("upsert auth", f"http={r5.status_code} {r5.text[:200]}")


async def test_db_binding_and_public_if_any() -> None:
    section("5. DB binding + public personas/contact (nếu có website_token)")
    from sqlalchemy import select

    from app.core.config.database import async_session_maker
    from app.db.models import MessagingInboxBinding

    async with async_session_maker() as db:
        q = await db.execute(
            select(MessagingInboxBinding)
            .where(MessagingInboxBinding.is_active.is_(True))
            .limit(5)
        )
        rows = list(q.scalars().all())

    if not rows:
        bad("no active inbox bindings", "skip live public test")
        return
    ok(f"found {len(rows)} binding(s)", f"first inbox={rows[0].inbox_id}")

    binding = rows[0]
    token = binding.website_token
    async with httpx.AsyncClient(base_url=BASE, timeout=30.0) as client:
        r = await client.get(f"/api/v1/public/live-chat/{token}/personas")
        body = r.json() if r.content else {}
        if body.get("status") == "success":
            data = body.get("data") or {}
            if "contact_capture" in data:
                ok(
                    "personas includes contact_capture",
                    json.dumps(data["contact_capture"], ensure_ascii=False)[:120],
                )
            else:
                bad("personas missing contact_capture", str(list(data.keys())))
        else:
            bad("personas live", f"http={r.status_code} {body.get('message')}")

        # Submit contact — expect success persisted if Redis up
        sid = f"oh_sim_{uuid4().hex[:16]}"
        r2 = await client.post(
            f"/api/v1/public/live-chat/{token}/contact",
            json={
                "client_session_id": sid,
                "name": "Sim User",
                "email": "sim@example.com",
                "phone": "0911222333",
            },
        )
        body2 = r2.json() if r2.content else {}
        if body2.get("status") == "success":
            data2 = body2.get("data") or {}
            ok(
                "POST public contact success",
                f"persisted={data2.get('persisted')} session={data2.get('client_session_id')}",
            )
            # verify redis consume path
            from app.services.v1.handle_chatwoot.contact_capture import consume_pending_contact
            from uuid import UUID

            pending = await consume_pending_contact(
                website_token=token,
                client_session_ids=[data2.get("client_session_id") or sid],
                expected_tenant_id=UUID(str(binding.tenant_id)),
            )
            if pending and pending.get("contact", {}).get("name") == "Sim User":
                ok("pending in Redis after public POST")
            else:
                # persisted false?
                if data2.get("persisted") is False:
                    bad("Redis persist", "API returned persisted=false")
                else:
                    bad("pending after POST", str(pending))
        else:
            # may fail validation if policy requires fields and mode off with empty required
            ok_or = body2.get("message", "")
            if "không hợp lệ" in (ok_or or "").lower() or body2.get("status") == "error":
                # If policy enabled with required and we sent all — shouldn't fail
                bad("POST public contact", f"{body2.get('status_code')} {ok_or} {body2.get('data')}")
            else:
                bad("POST public contact unexpected", str(body2)[:300])


async def test_apply_contact_to_inbox_payload_preserves_other_keys() -> None:
    section("6. Edge: apply preserves other PATCH keys")
    from app.services.v1.handle_chatwoot.contact_capture import (
        apply_contact_capture_to_inbox_payload,
    )

    out = apply_contact_capture_to_inbox_payload(
        {
            "name": "Inbox A",
            "greeting_enabled": True,
            "channel": {"website_url": "https://x.com"},
            "contact_capture": {
                "enabled": True,
                "mode": "pre_chat",
                "fields": [
                    {"key": "name", "enabled": True, "required": True},
                    {"key": "email", "enabled": True, "required": False},
                ],
            },
        }
    )
    if out.get("name") == "Inbox A" and out.get("greeting_enabled") is True:
        ok("preserves name/greeting")
    else:
        bad("preserve keys", str(out))
    ch = out.get("channel") if isinstance(out.get("channel"), dict) else {}
    if ch.get("website_url"):
        ok("preserves channel")
    else:
        bad("preserve channel", str(out.get("channel")))
    if ch.get("pre_chat_form_enabled") is True and "contact_capture" not in out:
        ok("injects channel.pre_chat, removes contact_capture")
    else:
        bad("inject pre_chat", str(ch))


def test_import_app_modules() -> None:
    section("0. Import modules (reload safety)")
    try:
        from app.services.v1.handle_chatwoot import upsert_livechat_contact  # noqa: F401
        from app.services.v1.handle_live_chat_public import submit_public_contact  # noqa: F401
        from app.api.v1.endpoints.live_chat_public import LiveChatContactBody  # noqa: F401
        from app.schemas.requests.chatwoot import LivechatContactUpsertBody  # noqa: F401

        ok("imports")
    except Exception as e:
        bad("imports", f"{e}\n{traceback.format_exc()}")


async def amain() -> int:
    print(f"Contact capture simulation against {BASE}")
    test_import_app_modules()
    test_unit_helpers()
    try:
        await test_bot_mode_redis_roundtrip()
    except Exception as e:
        bad("bot redis section crashed", f"{e}")
    await test_apply_contact_to_inbox_payload_preserves_other_keys()
    try:
        await test_redis_pending()
    except Exception as e:
        bad("redis section crashed", f"{e}\n{traceback.format_exc()}")
    try:
        await test_webhook_apply_logic()
    except Exception as e:
        bad("webhook section crashed", f"{e}\n{traceback.format_exc()}")
    try:
        await test_http_routes()
    except Exception as e:
        bad("http section crashed", f"{e}\n{traceback.format_exc()}")
    try:
        await test_db_binding_and_public_if_any()
    except Exception as e:
        bad("db/public section crashed", f"{e}\n{traceback.format_exc()}")

    print(f"\n=== RESULT: {PASS} passed, {FAIL} failed ===")
    for e in ERRORS:
        print(f"  • {e}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(amain()))
