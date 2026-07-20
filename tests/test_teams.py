import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4
from datetime import datetime, timezone

from app.db.models import User, ChatwootLegacyMap, ChatwootMapResourceType
from app.schemas.requests.chatwoot import (
    ChatwootTeamCreateBody,
    ChatwootTeamUpdateBody,
    ChatwootTeamMembersBody,
)
from app.services.v1.handle_chatwoot.teams import (
    _ensure_tenant_team_map,
    _map_tenant_team_by_local,
    _chatwoot_team_public,
    list_teams,
    create_team,
    get_team,
    update_team,
    delete_team,
    list_team_members,
    add_team_members,
    remove_team_members,
    update_team_members,
)
from app.schemas.responses.api_response_rule import ResponseStatus, ResponseStatusCode


@pytest.mark.asyncio
async def test_ensure_tenant_team_map_creates_new():
    db = AsyncMock()
    tenant_id = uuid4()
    cw_id = 12345

    # Mock no existing map
    db.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=None)))

    row = await _ensure_tenant_team_map(db, tenant_id, cw_id)
    assert row.resource_type == ChatwootMapResourceType.TEAM
    assert row.chatwoot_id == cw_id
    assert row.tenant_id == tenant_id
    db.add.assert_called_once()
    db.flush.assert_called_once()


@pytest.mark.asyncio
async def test_map_tenant_team_by_local_finds_existing():
    db = AsyncMock()
    tenant_id = uuid4()
    local_id = uuid4()

    mock_row = ChatwootLegacyMap(
        resource_type=ChatwootMapResourceType.TEAM,
        local_uuid=local_id,
        chatwoot_id=9876,
        tenant_id=tenant_id,
    )
    db.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=mock_row)))

    row = await _map_tenant_team_by_local(db, tenant_id, local_id)
    assert row is not None
    assert row.chatwoot_id == 9876


def test_chatwoot_team_public_redacts_ids():
    team_data = {
        "id": 123,
        "account_id": 456,
        "name": "Support Team",
        "description": "General customer support",
    }
    local_uuid = uuid4()
    public_team = _chatwoot_team_public(team_data, local_uuid)

    assert "account_id" not in public_team
    assert public_team["id"] == str(local_uuid)
    assert public_team["name"] == "Support Team"


@pytest.mark.asyncio
@patch("app.services.v1.handle_chatwoot.teams._require_tenant_access", return_value=None)
@patch("app.services.v1.handle_chatwoot.teams._resolve_account_id", return_value=(456, MagicMock()))
@patch("app.integrations.chatwoot.client.application_request")
async def test_list_teams_success(mock_req, mock_resolve, mock_access):
    db = AsyncMock()
    tenant_id = uuid4()
    current_user = User(id=uuid4(), tenant_id=tenant_id)
    request = MagicMock()
    request.query_params.multi_items = MagicMock(return_value=[])

    mock_req.return_value = MagicMock(
        status_code=200,
        data=[
            {"id": 123, "name": "Team 1", "description": "Desc 1"},
            {"id": 124, "name": "Team 2", "description": "Desc 2"},
        ],
    )

    # Mock ensure mapping helper returns mapping rows
    with patch(
        "app.services.v1.handle_chatwoot.teams._ensure_tenant_team_map",
        side_effect=lambda db, t_id, cw_id: ChatwootLegacyMap(
            resource_type=ChatwootMapResourceType.TEAM,
            local_uuid=uuid4(),
            chatwoot_id=cw_id,
            tenant_id=t_id,
        ),
    ):
        response = await list_teams(request, current_user, tenant_id, db)
        assert response["status"] == ResponseStatus.SUCCESS
        assert len(response["data"]["teams"]) == 2
        assert response["data"]["teams"][0]["name"] == "Team 1"
        db.commit.assert_called_once()


@pytest.mark.asyncio
@patch("app.services.v1.handle_chatwoot.teams._require_tenant_access", return_value=None)
@patch("app.services.v1.handle_chatwoot.teams._resolve_account_id", return_value=(456, MagicMock()))
@patch("app.integrations.chatwoot.client.application_request")
async def test_create_team_success(mock_req, mock_resolve, mock_access):
    db = AsyncMock()
    tenant_id = uuid4()
    current_user = User(id=uuid4(), tenant_id=tenant_id)
    request = MagicMock()
    request.query_params.multi_items = MagicMock(return_value=[])

    mock_req.return_value = MagicMock(
        status_code=201,
        data={"id": 789, "name": "New Team", "description": "Desc"},
    )

    body = ChatwootTeamCreateBody(name="New Team", description="Desc")

    with patch(
        "app.services.v1.handle_chatwoot.teams._ensure_tenant_team_map",
        return_value=ChatwootLegacyMap(
            resource_type=ChatwootMapResourceType.TEAM,
            local_uuid=uuid4(),
            chatwoot_id=789,
            tenant_id=tenant_id,
        ),
    ):
        response = await create_team(request, current_user, tenant_id, body, db)
        assert response["status"] == ResponseStatus.SUCCESS
        assert response["data"]["team"]["name"] == "New Team"
        db.commit.assert_called_once()


@pytest.mark.asyncio
@patch("app.services.v1.handle_chatwoot.teams._require_tenant_access", return_value=None)
@patch("app.services.v1.handle_chatwoot.teams._resolve_account_id", return_value=(456, MagicMock()))
@patch("app.services.v1.handle_chatwoot.teams._map_tenant_team_by_local")
@patch("app.integrations.chatwoot.client.application_request")
async def test_delete_team_success(mock_req, mock_map, mock_resolve, mock_access):
    db = AsyncMock()
    tenant_id = uuid4()
    team_uuid = uuid4()
    current_user = User(id=uuid4(), tenant_id=tenant_id)
    request = MagicMock()
    request.query_params.multi_items = MagicMock(return_value=[])

    mock_map.return_value = ChatwootLegacyMap(
        resource_type=ChatwootMapResourceType.TEAM,
        local_uuid=team_uuid,
        chatwoot_id=789,
        tenant_id=tenant_id,
    )

    mock_req.return_value = MagicMock(status_code=204, data=None)

    response = await delete_team(request, current_user, tenant_id, team_uuid, db)
    assert response["status"] == ResponseStatus.SUCCESS
    assert response["data"]["removed_team_id"] == str(team_uuid)
    db.delete.assert_called_once()
    db.commit.assert_called_once()
