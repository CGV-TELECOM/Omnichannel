from typing import Optional
from fastapi import APIRouter, Depends, Request, Query
from app.core.config.database import get_db
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.security.permissions import has_permission
from app.services.v1 import handle_tenant 
from app.schemas.requests.tenant import (
    TenantCreate,
    TenantKgAgentsReplaceBody,
    TenantOwnSettingsUpdate,
    TenantUpdate,
)
from uuid import UUID
from app.db.models import User 
from app.core.dependencies.dependencies import get_current_user_dependency
from app.core.config.logging import log_user_action


router = APIRouter(prefix ="/tenants", tags=["Tenant"])


@router.get("")
async def getAllTenant(
    request: Request,
    page: int = Query(1, ge=1, description="Số trang"),
    id: Optional[UUID] = Query(None, description="ID của tenant"),
    graph_id: Optional[UUID] = Query(None, description="ID của graph kg"),
    kg_agent_id: Optional[UUID] = Query(None, description="Lọc tenant có gắn KG agent này"),
    is_active: Optional[int] = Query(None, description="Trạng thái kích hoạt (0: chưa kích hoạt, 1: đã kích hoạt)"),
    graph_activated: Optional[int] = Query(None, description="Trạng thái kích hoạt graph (0: chưa kích hoạt, 1: đã kích hoạt)"),
    page_size: int = Query(10, ge=1, le=100, description="Số bản ghi mỗi trang"),
    search: Optional[str] = Query(None, description="Từ khóa tìm kiếm"),
    _ = Depends(has_permission("view_tenants")),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user_dependency)
): 
    return await handle_tenant.getAllTenant(request, current_user, id, graph_id, kg_agent_id, is_active, graph_activated, page, page_size, search, db)

@router.post("")
@log_user_action("createTenant")
async def createTenant(
    request: Request,
    tenant_data : TenantCreate,
    _ = Depends(has_permission("create_tenant")),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user_dependency)
):
    return await handle_tenant.createTenant(request, current_user, tenant_data, db)


@router.get("/me/settings")
async def getOwnTenantSettings(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user_dependency),
    _=Depends(has_permission("view_own_tenant_settings")),
):
    """Cài đặt vận hành tenant hiện tại (CSAT, chatbot) — admin-partner."""
    return await handle_tenant.getOwnTenantSettings(current_user, db)


@router.patch("/me/settings")
@log_user_action("updateOwnTenantSettings")
async def updateOwnTenantSettings(
    request: Request,
    settings_data: TenantOwnSettingsUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user_dependency),
    _=Depends(has_permission("edit_own_tenant_settings")),
):
    """Chỉ cập nhật conversation_rating_enabled / chatbot_enabled / default_responder."""
    return await handle_tenant.updateOwnTenantSettings(
        current_user, settings_data, db
    )


@router.get("/{tenant_id}/kg-agents")
async def list_tenant_kg_agents(
    tenant_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user_dependency),
    _=Depends(has_permission("view_tenants")),
):
    return await handle_tenant.listTenantKgAgents(tenant_id, current_user, db)


@router.put("/{tenant_id}/kg-agents")
@log_user_action("replaceTenantKgAgents")
async def replace_tenant_kg_agents(
    tenant_id: UUID,
    request: Request,
    body: TenantKgAgentsReplaceBody,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user_dependency),
    _=Depends(has_permission("edit_tenant")),
):
    return await handle_tenant.replaceTenantKgAgents(
        tenant_id, current_user, body, db
    )


@router.put("/{tenant_id}")
@log_user_action("updateTenant")
async def updateTenant(
    tenant_id: UUID,
    request: Request,
    tenant_data : TenantUpdate,
    _ = Depends(has_permission("edit_tenant")),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user_dependency)
):
    return await handle_tenant.updateTenant(tenant_id, current_user, request, tenant_data, db)


@router.delete("/{tenant_id}")
@log_user_action("deleteTenant")
async def deleteTenant(
    tenant_id: UUID,
    request: Request,
    _ = Depends(has_permission("delete_tenant")),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user_dependency)
):
    return await handle_tenant.deleteTenant(tenant_id, current_user, request, db)