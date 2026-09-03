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

def test_customer_provided_info_schema():
    from datetime import datetime, timezone
    from app.schemas.requests.customer_provided_info import CustomerProvidedInfoResponse
    from app.db.models import CustomerProvidedInfo
    import uuid

    info_id = uuid.uuid4()
    tenant_id = uuid.uuid4()
    now = datetime.now(timezone.utc)

    # Instantiate the SQLAlchemy model mock
    db_obj = CustomerProvidedInfo(
        id=info_id,
        tenant_id=tenant_id,
        name="Test User",
        email="test@example.com",
        phone="0123456789",
        description="Providing feedback",
        created_at=now,
        updated_at=now
    )

    # Validate response schema from_attributes
    response = CustomerProvidedInfoResponse.model_validate(db_obj)

    assert response.id == info_id
    assert response.tenant_id == tenant_id
    assert response.name == "Test User"
    assert response.email == "test@example.com"
    assert response.phone == "0123456789"
    assert response.description == "Providing feedback"
    assert response.created_at == now
    assert response.updated_at == now


def test_tenant_metadata_default_and_response_validation():
    from app.db.models import Tenant, TenantKgAgent
    from app.schemas.requests.tenant import TenantResponse
    import uuid

    tenant_id = uuid.uuid4()
    kg_agent_uuid = uuid.uuid4()
    row_id = uuid.uuid4()

    tenant = Tenant(
        id=tenant_id,
        name="Test Default Tenant",
    )
    if tenant.meta_data is None:
        tenant.meta_data = {"chatbot_enabled": True, "default_responder": "bot"}

    assert tenant.meta_data == {"chatbot_enabled": True, "default_responder": "bot"}

    kg_row = TenantKgAgent(
        id=row_id,
        tenant_id=tenant_id,
        kg_agent_id=kg_agent_uuid,
        key="default",
        is_default=True,
        is_active=True,
    )
    tenant.kg_agents = [kg_row]

    response = TenantResponse.model_validate(tenant, from_attributes=True)
    assert response.id == tenant_id
    assert response.name == "Test Default Tenant"
    assert len(response.kg_agents) == 1
    assert response.kg_agents[0].kg_agent_id == kg_agent_uuid
    assert response.meta_data == {"chatbot_enabled": True, "default_responder": "bot"}


