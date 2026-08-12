"""
API báo cáo dashboard messaging (proxy Chatwoot Reports).

Tất cả route yêu cầu JWT + permission `view_messaging_reports`,
tenant scoping như các module messaging khác.
"""
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config.database import get_db
from app.core.dependencies.dependencies import get_current_user_dependency
from app.core.security.permissions import has_permission
from app.db.models import User
from app.services.v1.handle_chatwoot import reports as handle_reports

router = APIRouter()


@router.get("/tenants/{tenant_id}/reports/overview")
async def dashboard_overview(
    request: Request,
    tenant_id: UUID,
    since: Optional[str] = Query(None, description="Unix epoch (giây) hoặc ISO date"),
    until: Optional[str] = Query(None, description="Unix epoch (giây) hoặc ISO date"),
    _=Depends(has_permission("view_messaging_reports")),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user_dependency),
):
    """Gộp summary + realtime conversation metrics + CSAT trong 1 response."""
    return await handle_reports.get_dashboard_overview(
        request, current_user, tenant_id, db, since=since, until=until
    )


@router.get("/tenants/{tenant_id}/reports")
async def report_timeseries(
    request: Request,
    tenant_id: UUID,
    metric: str = Query(
        ...,
        description=(
            "conversations_count | incoming_messages_count | outgoing_messages_count | "
            "avg_first_response_time | avg_resolution_time | resolutions_count | "
            "bot_resolutions_count | bot_handoffs_count | reply_time"
        ),
    ),
    type: str = Query("account", description="account | agent | inbox | label | team"),
    id: Optional[str] = Query(
        None,
        description="Id đối tượng khi type != account (agent/team nhận UUID nội bộ)",
    ),
    since: Optional[str] = Query(None, description="Unix epoch (giây) hoặc ISO date"),
    until: Optional[str] = Query(None, description="Unix epoch (giây) hoặc ISO date"),
    group_by: Optional[str] = Query(None, description="day | week | month | year"),
    business_hours: Optional[bool] = Query(None, description="Chỉ tính trong giờ làm việc"),
    _=Depends(has_permission("view_messaging_reports")),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user_dependency),
):
    """Timeseries {value, timestamp} theo metric — vẽ chart."""
    return await handle_reports.get_report_timeseries(
        request,
        current_user,
        tenant_id,
        db,
        metric=metric,
        report_type=type,
        scope_id=id,
        since=since,
        until=until,
        group_by=group_by,
        business_hours=business_hours,
    )


@router.get("/tenants/{tenant_id}/reports/summary")
async def report_summary(
    request: Request,
    tenant_id: UUID,
    type: str = Query("account", description="account | agent | inbox | label | team"),
    id: Optional[str] = Query(
        None, description="Id đối tượng khi type != account (agent/team nhận UUID nội bộ)"
    ),
    since: Optional[str] = Query(None, description="Unix epoch (giây) hoặc ISO date"),
    until: Optional[str] = Query(None, description="Unix epoch (giây) hoặc ISO date"),
    business_hours: Optional[bool] = Query(None),
    _=Depends(has_permission("view_messaging_reports")),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user_dependency),
):
    """Số liệu tổng hợp kỳ hiện tại + `previous` (kỳ trước) — card % tăng/giảm."""
    return await handle_reports.get_report_summary(
        request,
        current_user,
        tenant_id,
        db,
        report_type=type,
        scope_id=id,
        since=since,
        until=until,
        business_hours=business_hours,
    )


@router.get("/tenants/{tenant_id}/reports/conversations")
async def conversation_metrics_account(
    request: Request,
    tenant_id: UUID,
    _=Depends(has_permission("view_messaging_reports")),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user_dependency),
):
    """Realtime: open / unattended / unassigned toàn account."""
    return await handle_reports.get_conversation_metrics_account(
        request, current_user, tenant_id, db
    )


@router.get("/tenants/{tenant_id}/reports/conversations/agents")
async def conversation_metrics_agents(
    request: Request,
    tenant_id: UUID,
    agent_id: Optional[str] = Query(
        None, description="UUID agent nội bộ (bỏ trống = tất cả agent)"
    ),
    _=Depends(has_permission("view_messaging_reports")),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user_dependency),
):
    """Workload theo agent (id trả về là UUID nội bộ)."""
    return await handle_reports.get_conversation_metrics_agents(
        request, current_user, tenant_id, db, agent_id=agent_id
    )


@router.get("/tenants/{tenant_id}/reports/conversation-traffic")
async def conversation_traffic(
    request: Request,
    tenant_id: UUID,
    timezone_offset: Optional[str] = Query(
        None, description="Offset giờ, ví dụ 7 cho UTC+7"
    ),
    since: Optional[str] = Query(None),
    until: Optional[str] = Query(None),
    _=Depends(has_permission("view_messaging_reports")),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user_dependency),
):
    """Traffic hội thoại theo giờ/ngày — heatmap."""
    return await handle_reports.get_conversation_traffic(
        request,
        current_user,
        tenant_id,
        db,
        timezone_offset=timezone_offset,
        since=since,
        until=until,
    )


@router.get("/tenants/{tenant_id}/reports/summary/{kind}")
async def grouped_summary_report(
    request: Request,
    tenant_id: UUID,
    kind: str,
    since: Optional[str] = Query(None),
    until: Optional[str] = Query(None),
    business_hours: Optional[bool] = Query(None),
    _=Depends(has_permission("view_messaging_reports")),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user_dependency),
):
    """Summary theo nhóm: agent | team | label | channel (Chatwoot >= 4.10)."""
    return await handle_reports.get_grouped_summary_report(
        request,
        current_user,
        tenant_id,
        db,
        kind=kind,
        since=since,
        until=until,
        business_hours=business_hours,
    )


@router.get("/tenants/{tenant_id}/reports/csat/metrics")
async def csat_metrics(
    request: Request,
    tenant_id: UUID,
    since: Optional[str] = Query(None),
    until: Optional[str] = Query(None),
    agent_id: Optional[str] = Query(None, description="UUID agent nội bộ"),
    _=Depends(has_permission("view_messaging_reports")),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user_dependency),
):
    """CSAT: tổng phản hồi, satisfaction score..."""
    return await handle_reports.get_csat_metrics(
        request, current_user, tenant_id, db, since=since, until=until, agent_id=agent_id
    )


@router.get("/tenants/{tenant_id}/reports/csat")
async def csat_responses(
    request: Request,
    tenant_id: UUID,
    page: int = Query(1, ge=1),
    since: Optional[str] = Query(None),
    until: Optional[str] = Query(None),
    agent_id: Optional[str] = Query(None, description="UUID agent nội bộ"),
    _=Depends(has_permission("view_messaging_reports")),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user_dependency),
):
    """Danh sách từng phản hồi CSAT (phân trang)."""
    return await handle_reports.list_csat_responses(
        request,
        current_user,
        tenant_id,
        db,
        page=page,
        since=since,
        until=until,
        agent_id=agent_id,
    )
