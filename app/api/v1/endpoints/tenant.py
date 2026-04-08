from typing import Optional
from fastapi import APIRouter, Depends, Request, Query
from app.core.config.database import get_db
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.security.permissions import has_permission
from app.services.v1 import handle_tenant 
from app.schemas.requests.tenant import TenantCreate, TenantUpdate
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
    page_size: int = Query(10, ge=1, le=100, description="Số bản ghi mỗi trang"),
    search: Optional[str] = Query(None, description="Từ khóa tìm kiếm"),
    _ = Depends(has_permission("view_roles")),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user_dependency)
): 
    return await handle_tenant.getAllTenant(request, current_user, id, page, page_size, search, db)

@router.post("")
@log_user_action("createTenant")
async def createTenant(
    request: Request,
    tenant_data : TenantCreate,
    _ = Depends(has_permission("view_roles")),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user_dependency)
):
    return await handle_tenant.createTenant(request, current_user, tenant_data, db)


@router.put("/{tenant_id}")
@log_user_action("updateTenant")
async def updateTenant(
    tenant_id: UUID,
    request: Request,
    tenant_data : TenantUpdate,
    _ = Depends(has_permission("view_roles")),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user_dependency)
):
    return await handle_tenant.updateTenant(tenant_id, current_user, request, tenant_data, db)


@router.delete("/{tenant_id}")
@log_user_action("deleteTenant")
async def deleteTenant(
    tenant_id: UUID,
    request: Request,
    _ = Depends(has_permission("view_roles")),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user_dependency)
):
    return await handle_tenant.deleteTenant(tenant_id, current_user, request, db)