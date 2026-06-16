import pytest
from uuid import UUID
from app.services.v1.handle_chatwoot._shared import _walk_redact_agent_refs

def test_walk_redact_agent_refs():
    # Setup mock mapping
    local_uuid = UUID("11111111-1111-1111-1111-111111111111")
    cw_map = {123: local_uuid}

    # Test payload for message created with agent sender
    payload = {
        "event": "message_created",
        "sender_type": "Agent",
        "sender": {
            "id": 123,
            "name": "Test Agent",
            "type": "agent"
        },
        "conversation": {
            "id": 456,
            "assignee": {
                "id": 123,
                "name": "Test Agent",
                "type": "agent"
            }
        }
    }

    # Execute
    res = _walk_redact_agent_refs(payload, cw_map)

    # Assertions
    assert res["sender"]["id"] == str(local_uuid)
    assert res["conversation"]["assignee"]["id"] == str(local_uuid)
    assert "account_id" not in res
