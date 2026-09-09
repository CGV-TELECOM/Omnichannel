"""
API search messaging (proxy Chatwoot Search).

Map FE/Chatwoot UI:
  GET /api/v1/accounts/{id}/search/*
→ GET /api/v1/messaging/tenants/{tenant_id}/search/*
"""

from uuid import UUID

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config.database import get_db
from app.core.dependencies.dependencies import get_current_user_dependency
from app.core.security.permissions import has_permission
from app.db.models import User
from app.services.v1.handle_chatwoot import search as handle_search

router = APIRouter()


@router.get("/tenants/{tenant_id}/search")
async def messaging_search_all(
    request: Request,
    tenant_id: UUID,
    _=Depends(has_permission("view_messaging_conversations")),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user_dependency),
):
    """Search tổng hợp — forward Chatwoot GET .../search."""
    return await handle_search.search_all(request, current_user, tenant_id, db)


@router.get("/tenants/{tenant_id}/search/contacts")
async def messaging_search_contacts(
    request: Request,
    tenant_id: UUID,
    _=Depends(has_permission("view_messaging_conversations")),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user_dependency),
):
    """UI tab Contacts — forward GET .../search/contacts?q=&page=."""
    return await handle_search.search_contacts(request, current_user, tenant_id, db)


@router.get("/tenants/{tenant_id}/search/conversations")
async def messaging_search_conversations(
    request: Request,
    tenant_id: UUID,
    _=Depends(has_permission("view_messaging_conversations")),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user_dependency),
):
    """UI tab Conversations — forward GET .../search/conversations."""
    return await handle_search.search_conversations(
        request, current_user, tenant_id, db
    )


@router.get("/tenants/{tenant_id}/search/messages")
async def messaging_search_messages(
    request: Request,
    tenant_id: UUID,
    _=Depends(has_permission("view_messaging_conversations")),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user_dependency),
):
    """UI tab Messages — forward GET .../search/messages."""
    return await handle_search.search_messages(request, current_user, tenant_id, db)


@router.get("/tenants/{tenant_id}/search/articles")
async def messaging_search_articles(
    request: Request,
    tenant_id: UUID,
    _=Depends(has_permission("view_messaging_conversations")),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user_dependency),
):
    """UI tab Articles — forward GET .../search/articles."""
    return await handle_search.search_articles(request, current_user, tenant_id, db)


@router.get("/tenants/{tenant_id}/contacts/search")
async def messaging_contacts_search(
    request: Request,
    tenant_id: UUID,
    _=Depends(has_permission("view_messaging_conversations")),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user_dependency),
):
    """Application API Search Contacts — GET .../contacts/search?q=&sort=&page=."""
    return await handle_search.contacts_search(request, current_user, tenant_id, db)


@router.get("/tenants/{tenant_id}/conversations/search")
async def messaging_conversations_search(
    request: Request,
    tenant_id: UUID,
    _=Depends(has_permission("view_messaging_conversations")),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user_dependency),
):
    """Application API — GET .../conversations/search."""
    return await handle_search.conversations_search(
        request, current_user, tenant_id, db
    )


@router.get("/tenants/{tenant_id}/companies/search")
async def messaging_companies_search(
    request: Request,
    tenant_id: UUID,
    _=Depends(has_permission("view_messaging_conversations")),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user_dependency),
):
    """Application API — GET .../companies/search."""
    return await handle_search.companies_search(request, current_user, tenant_id, db)
