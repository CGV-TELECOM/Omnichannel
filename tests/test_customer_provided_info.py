import pytest
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4
from datetime import datetime, timezone

from app.db.models import User, CustomerProvidedInfo
from app.schemas.requests.customer_provided_info import (
    CustomerProvidedInfoCreate,
    CustomerProvidedInfoUpdate,
)
from app.services.v1.handle_customer_provided_info import (
    get_customer_provided_info,
    create_customer_provided_info,
    update_customer_provided_info,
    delete_customer_provided_info,
)
from app.schemas.responses.api_response_rule import ResponseStatus, ResponseStatusCode

@pytest.mark.asyncio
async def test_create_customer_provided_info_success():
    db = AsyncMock()
    
    # Mock tenant check: return a tenant object
    db.scalar = AsyncMock(return_value=MagicMock(is_active=1))
    
    def mock_refresh(info):
        info.id = uuid4()
        info.created_at = datetime.now(timezone.utc)
        info.updated_at = datetime.now(timezone.utc)
    db.refresh = AsyncMock(side_effect=mock_refresh)
    
    current_user = User(
        id=uuid4(),
        tenant_id=uuid4(),
        role_id=uuid4(),
        level_id=uuid4(),
    )
    
    # Mock isCheckMaxLevel to return False (non-admin)
    with MagicMock() as mock_check_level:
        # Patch the helper function
        import app.services.v1.handle_customer_provided_info as handler
        handler.isCheckMaxLevel = AsyncMock(return_value=False)
        
        info_data = CustomerProvidedInfoCreate(
            name="Alice",
            email="alice@example.com",
            phone="123456789",
            description="Details",
            tenant_id=current_user.tenant_id
        )
        
        response = await create_customer_provided_info(info_data, db, current_user)
        print("DEBUG RESPONSE IS:", response)
        assert response["status"] == ResponseStatus.SUCCESS
        assert response["status_code"] == ResponseStatusCode.CREATED
        assert response["data"]["name"] == "Alice"
        assert response["data"]["email"] == "alice@example.com"
        assert response["data"]["phone"] == "123456789"
        assert response["data"]["description"] == "Details"
        assert response["data"]["tenant_id"] == str(current_user.tenant_id)

@pytest.mark.asyncio
async def test_create_customer_provided_info_wrong_tenant():
    db = AsyncMock()
    current_user = User(
        id=uuid4(),
        tenant_id=uuid4(),
    )
    
    # Mock isCheckMaxLevel to return False
    import app.services.v1.handle_customer_provided_info as handler
    handler.isCheckMaxLevel = AsyncMock(return_value=False)
    
    info_data = CustomerProvidedInfoCreate(
        name="Alice",
        tenant_id=uuid4()  # different tenant
    )
    
    response = await create_customer_provided_info(info_data, db, current_user)
    assert response["status"] == ResponseStatus.ERROR
    assert response["status_code"] == ResponseStatusCode.FORBIDDEN
    assert "tenant" in response["message"]

@pytest.mark.asyncio
async def test_delete_customer_provided_info_success():
    db = AsyncMock()
    
    current_user = User(
        id=uuid4(),
        tenant_id=uuid4(),
    )
    
    info = CustomerProvidedInfo(
        id=uuid4(),
        tenant_id=current_user.tenant_id,
        name="Alice",
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc)
    )
    
    db.scalar = AsyncMock(return_value=info)
    
    # Mock isCheckMaxLevel to return False
    import app.services.v1.handle_customer_provided_info as handler
    handler.isCheckMaxLevel = AsyncMock(return_value=False)
    
    response = await delete_customer_provided_info(info.id, db, current_user)
    
    assert response["status"] == ResponseStatus.SUCCESS
    assert response["status_code"] == ResponseStatusCode.OK
    assert response["message"] == "Xóa thông tin thành công"
    db.delete.assert_called_once_with(info)
