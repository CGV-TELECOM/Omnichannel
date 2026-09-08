import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

from app.services.v1.handle_chatwoot.notifications import (
    extract_message_preview,
    extract_sender_display_name,
    notify_chatwoot_conversation_assignment,
    notify_chatwoot_incoming_message,
    resolve_agent_local_uuid,
)


@pytest.mark.asyncio
async def test_resolve_agent_local_uuid_in_memory():
    db = AsyncMock()
    tenant_id = uuid4()
    local_uuid = uuid4()
    cw_map = {123: local_uuid}

    res = await resolve_agent_local_uuid(db, tenant_id, 123, cw_map)
    assert res == local_uuid
    db.execute.assert_not_called()


@pytest.mark.asyncio
async def test_resolve_agent_local_uuid_db_scoped():
    db = AsyncMock()
    tenant_id = uuid4()
    local_uuid = uuid4()

    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = local_uuid
    db.execute = AsyncMock(return_value=mock_result)

    res = await resolve_agent_local_uuid(db, tenant_id, 456, cw_map={})
    assert res == local_uuid
    assert db.execute.call_count == 1


@pytest.mark.asyncio
async def test_resolve_agent_local_uuid_invalid():
    db = AsyncMock()
    tenant_id = uuid4()

    res = await resolve_agent_local_uuid(db, tenant_id, None)
    assert res is None

    res2 = await resolve_agent_local_uuid(db, tenant_id, "invalid_id")
    assert res2 is None


def test_extract_sender_display_name():
    # 1. sender dict in payload
    p1 = {"sender": {"name": "Nguyễn Văn A"}}
    assert extract_sender_display_name(p1) == "Nguyễn Văn A"

    # 2. meta.sender in conversation
    p2 = {"conversation": {"meta": {"sender": {"name": "Trần Thị B"}}}}
    assert extract_sender_display_name(p2) == "Trần Thị B"

    # 3. contact in conversation
    p3 = {"conversation": {"contact": {"name": "Lê Văn C"}}}
    assert extract_sender_display_name(p3) == "Lê Văn C"

    # 4. Fallback
    assert extract_sender_display_name({}) == "Khách hàng"
    assert extract_sender_display_name(None) == "Khách hàng"


def test_extract_message_preview():
    # 1. Text content
    p1 = {"content": "Xin chào OmniHub"}
    assert extract_message_preview(p1) == "Xin chào OmniHub"

    # 2. Long text truncated
    p2 = {"content": "A" * 150}
    preview = extract_message_preview(p2, max_len=10)
    assert preview == "AAAAAAAAAA..."

    # 3. Attachments
    p3 = {"attachments": [{"file_type": "image"}]}
    assert extract_message_preview(p3) == "[Hình ảnh]"

    p4 = {"attachments": [{"file_type": "audio"}]}
    assert extract_message_preview(p4) == "[Tin nhắn thoại]"

    p5 = {"attachments": [{"file_type": "video"}]}
    assert extract_message_preview(p5) == "[Video]"

    p6 = {"attachments": [{"file_type": "file"}]}
    assert extract_message_preview(p6) == "[Tệp đính kèm]"

    # 4. Empty fallback
    assert extract_message_preview({}) == "Tin nhắn mới"


@pytest.mark.asyncio
async def test_notify_chatwoot_incoming_message_success():
    db = AsyncMock()
    tenant_id = uuid4()
    agent_uuid = uuid4()

    payload = {
        "id": 101,
        "content": "Tôi cần hỗ trợ tư vấn",
        "sender": {"name": "Khách VIP"},
        "conversation": {
            "id": 999,
            "inbox_id": 5,
            "assignee_id": 77,
        },
    }
    cw_map = {77: agent_uuid}

    with patch(
        "app.services.v1.handle_chatwoot.notifications.is_bot_assignee",
        new=AsyncMock(return_value=False),
    ), patch(
        "app.services.v1.handle_notification.NotificationService.send_notification_to_user",
        new=AsyncMock(return_value=True),
    ) as mock_send:
        ok = await notify_chatwoot_incoming_message(db, tenant_id, payload, cw_map)
        assert ok is True
        mock_send.assert_awaited_once()
        call_kwargs = mock_send.await_args.kwargs
        assert call_kwargs["user_id"] == agent_uuid
        assert "Khách VIP" in call_kwargs["title"]
        assert call_kwargs["message"] == "Tôi cần hỗ trợ tư vấn"
        assert call_kwargs["data"]["conversation_id"] == 999


@pytest.mark.asyncio
async def test_notify_chatwoot_incoming_message_skip_bot():
    db = AsyncMock()
    tenant_id = uuid4()

    payload = {
        "id": 102,
        "content": "Tin gửi bot",
        "conversation": {"id": 888, "assignee_id": 99},
    }

    with patch(
        "app.services.v1.handle_chatwoot.notifications.is_bot_assignee",
        new=AsyncMock(return_value=True),
    ), patch(
        "app.services.v1.handle_notification.NotificationService.send_notification_to_user",
        new=AsyncMock(),
    ) as mock_send:
        ok = await notify_chatwoot_incoming_message(db, tenant_id, payload)
        assert ok is False
        mock_send.assert_not_called()


@pytest.mark.asyncio
async def test_notify_chatwoot_incoming_message_unassigned():
    db = AsyncMock()
    tenant_id = uuid4()

    payload = {
        "id": 103,
        "content": "Tin chưa giao",
        "conversation": {"id": 777},
    }

    ok = await notify_chatwoot_incoming_message(db, tenant_id, payload)
    assert ok is False


@pytest.mark.asyncio
async def test_notify_chatwoot_conversation_assignment_success():
    db = AsyncMock()
    tenant_id = uuid4()
    agent_uuid = uuid4()

    conv = {
        "id": 555,
        "inbox_id": 2,
        "contact": {"name": "Nguyễn Thị Mai"},
    }
    cw_map = {88: agent_uuid}

    with patch(
        "app.services.v1.handle_chatwoot.notifications.is_bot_assignee",
        new=AsyncMock(return_value=False),
    ), patch(
        "app.services.v1.handle_notification.NotificationService.send_notification_to_user",
        new=AsyncMock(return_value=True),
    ) as mock_send:
        ok = await notify_chatwoot_conversation_assignment(
            db, tenant_id, conv, assignee_id=88, cw_map=cw_map
        )
        assert ok is True
        mock_send.assert_awaited_once()
        kwargs = mock_send.await_args.kwargs
        assert kwargs["user_id"] == agent_uuid
        assert "555" in kwargs["message"]
        assert "Nguyễn Thị Mai" in kwargs["message"]
