"""Proxy Chatwoot Search APIs (Application).

Chatwoot routes (account scoped):
  GET /search
  GET /search/contacts|conversations|messages|articles
  GET /contacts/search
  GET /conversations/search
  GET /companies/search

OmniHub: /api/v1/messaging/tenants/{tenant_id}/...
"""

from __future__ import annotations

from uuid import UUID

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import User
from app.services.v1.handle_chatwoot._shared import _tenant_application_forward


async def _forward_search(
    request: Request,
    current_user: User,
    tenant_id: UUID,
    db: AsyncSession,
    *,
    path_suffix: str,
    ok_message: str,
    error_message: str,
):
    return await _tenant_application_forward(
        current_user,
        tenant_id,
        db,
        request=request,
        method="GET",
        path_suffix=path_suffix,
        forward_all_query_params=True,
        redact_agents=True,
        ok_message=ok_message,
        error_message=error_message,
        agent_scoped=True,
    )


async def search_all(
    request: Request,
    current_user: User,
    tenant_id: UUID,
    db: AsyncSession,
):
    """GET .../search?q=&page= — search tổng hợp (nếu Chatwoot hỗ trợ)."""
    return await _forward_search(
        request,
        current_user,
        tenant_id,
        db,
        path_suffix="/search",
        ok_message="Kết quả tìm kiếm messaging",
        error_message="Không tìm kiếm được trên messaging",
    )


async def search_contacts(
    request: Request,
    current_user: User,
    tenant_id: UUID,
    db: AsyncSession,
):
    """GET .../search/contacts?q=&page= — UI global search Contacts tab."""
    return await _forward_search(
        request,
        current_user,
        tenant_id,
        db,
        path_suffix="/search/contacts",
        ok_message="Kết quả tìm kiếm contacts",
        error_message="Không tìm kiếm contacts trên messaging",
    )


async def search_conversations(
    request: Request,
    current_user: User,
    tenant_id: UUID,
    db: AsyncSession,
):
    """GET .../search/conversations?q=&page=."""
    return await _forward_search(
        request,
        current_user,
        tenant_id,
        db,
        path_suffix="/search/conversations",
        ok_message="Kết quả tìm kiếm conversations",
        error_message="Không tìm kiếm conversations trên messaging",
    )


async def search_messages(
    request: Request,
    current_user: User,
    tenant_id: UUID,
    db: AsyncSession,
):
    """GET .../search/messages?q=&page=."""
    return await _forward_search(
        request,
        current_user,
        tenant_id,
        db,
        path_suffix="/search/messages",
        ok_message="Kết quả tìm kiếm messages",
        error_message="Không tìm kiếm messages trên messaging",
    )


async def search_articles(
    request: Request,
    current_user: User,
    tenant_id: UUID,
    db: AsyncSession,
):
    """GET .../search/articles?q=&page=."""
    return await _forward_search(
        request,
        current_user,
        tenant_id,
        db,
        path_suffix="/search/articles",
        ok_message="Kết quả tìm kiếm articles",
        error_message="Không tìm kiếm articles trên messaging",
    )


async def contacts_search(
    request: Request,
    current_user: User,
    tenant_id: UUID,
    db: AsyncSession,
):
    """GET .../contacts/search?q=&sort=&page= — Application API Search Contacts."""
    return await _forward_search(
        request,
        current_user,
        tenant_id,
        db,
        path_suffix="/contacts/search",
        ok_message="Kết quả search contacts (resolved)",
        error_message="Không search contacts trên messaging",
    )


async def conversations_search(
    request: Request,
    current_user: User,
    tenant_id: UUID,
    db: AsyncSession,
):
    """GET .../conversations/search?q=&page=."""
    return await _forward_search(
        request,
        current_user,
        tenant_id,
        db,
        path_suffix="/conversations/search",
        ok_message="Kết quả search conversations",
        error_message="Không search conversations trên messaging",
    )


async def companies_search(
    request: Request,
    current_user: User,
    tenant_id: UUID,
    db: AsyncSession,
):
    """GET .../companies/search?q=&page=."""
    return await _forward_search(
        request,
        current_user,
        tenant_id,
        db,
        path_suffix="/companies/search",
        ok_message="Kết quả search companies",
        error_message="Không search companies trên messaging",
    )
