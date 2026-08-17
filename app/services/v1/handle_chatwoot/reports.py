"""
Proxy báo cáo/dashboard Chatwoot (Reports API v2 + CSAT + summary reports).

Endpoint gốc phía Chatwoot:
- GET /api/v2/accounts/{id}/reports                    — timeseries theo metric
- GET /api/v2/accounts/{id}/reports/summary            — tổng hợp kỳ + kỳ trước
- GET /api/v2/accounts/{id}/reports/conversations      — realtime open/unattended/unassigned
- GET /api/v2/accounts/{id}/reports/conversations/?type=agent — theo agent
- GET /api/v2/accounts/{id}/reports/conversation_traffic      — traffic theo giờ/ngày
- GET /api/v2/accounts/{id}/summary_reports/{agent|team|label|channel} — Chatwoot >= 4.10
- GET /api/v1/accounts/{id}/csat_survey_responses(/metrics)   — CSAT
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional
from uuid import UUID

from fastapi import Request
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import User
from app.integrations.chatwoot import client as chatwoot_client
from app.schemas.responses.api_response_rule import (
    ResponseStatus,
    ResponseStatusCode,
    api_response,
)
from sqlalchemy import and_, select

from app.db.models import ChatwootLegacyMap, ChatwootMapResourceType
from app.services.v1.handle_chatwoot._shared import (
    _application_error_http_status,
    _chatwoot_agent_id_to_local_map,
    _chatwoot_error_payload,
    _map_tenant_agent_by_local,
    _map_tenant_team_by_local,
    _require_tenant_access,
    _resolve_account_id,
)

logger = logging.getLogger(__name__)

# Metric hợp lệ của GET /api/v2/accounts/{id}/reports
REPORT_METRICS = frozenset(
    {
        "conversations_count",
        "incoming_messages_count",
        "outgoing_messages_count",
        "avg_first_response_time",
        "avg_resolution_time",
        "resolutions_count",
        "bot_resolutions_count",
        "bot_handoffs_count",
        "reply_time",
    }
)

REPORT_TYPES = frozenset({"account", "agent", "inbox", "label", "team"})

# group_by hợp lệ cho timeseries
REPORT_GROUP_BY = frozenset({"day", "week", "month", "year", "hour"})

# summary_reports/{kind} (Chatwoot >= 4.10)
SUMMARY_REPORT_KINDS = frozenset({"agent", "team", "label", "channel", "inbox"})


def _to_epoch_str(value: Optional[str]) -> Optional[str]:
    """
    Chatwoot nhận since/until là unix epoch (giây).
    Hỗ trợ FE gửi ISO date/datetime cho tiện: '2026-08-01' → epoch.
    """
    if not value:
        return None
    v = value.strip()
    if v.isdigit():
        return v
    try:
        dt = datetime.fromisoformat(v.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return str(int(dt.timestamp()))
    except ValueError:
        return v  # để Chatwoot tự báo lỗi nếu format lạ


def _bad_request(message: str) -> Any:
    return api_response(
        ResponseStatus.ERROR,
        ResponseStatusCode.BAD_REQUEST,
        message,
    )


async def _forward_report(
    current_user: User,
    tenant_id: UUID,
    db: AsyncSession,
    *,
    path_builder,
    params: list[tuple[str, str]],
    ok_message: str,
    redact_items: Optional[str] = None,  # "agent" | "team"
) -> Any:
    """
    Forward request báo cáo tới Chatwoot theo account đã map với tenant.
    path_builder(account_id) → path đầy đủ (v1 hoặc v2).
    """
    try:
        denied = await _require_tenant_access(current_user, tenant_id, db)
        if denied is not None:
            return denied

        account_id, _ = await _resolve_account_id(db, tenant_id)
        if account_id is None:
            return api_response(
                ResponseStatus.ERROR,
                ResponseStatusCode.NOT_FOUND,
                "Chưa có map messaging account cho tenant này",
            )

        path = path_builder(account_id)
        res = await chatwoot_client.application_request("GET", path, params=params)

        if res.status_code == 200:
            data: Any = res.data
            if redact_items:
                data = await _redact_metric_item_ids(db, tenant_id, data, kind=redact_items)
            return api_response(
                ResponseStatus.SUCCESS,
                ResponseStatusCode.OK,
                ok_message,
                {"tenant_id": str(tenant_id), "messaging": data},
            )

        return api_response(
            ResponseStatus.ERROR,
            _application_error_http_status(res.status_code),
            "Messaging trả lỗi khi lấy báo cáo",
            _chatwoot_error_payload(res),
        )
    except SQLAlchemyError as e:
        return api_response(
            ResponseStatus.ERROR,
            ResponseStatusCode.INTERNAL_SERVER_ERROR,
            f"Lỗi CSDL: {e}",
        )
    except Exception as e:
        logger.error("[chatwoot reports] %s", e)
        return api_response(
            ResponseStatus.ERROR,
            ResponseStatusCode.INTERNAL_SERVER_ERROR,
            f"Lỗi không xác định: {e}",
        )


async def _chatwoot_team_id_to_local_map(
    db: AsyncSession, tenant_id: UUID
) -> dict[int, UUID]:
    """Map id số team trên messaging → UUID nội bộ."""
    q = await db.execute(
        select(ChatwootLegacyMap.chatwoot_id, ChatwootLegacyMap.local_uuid).where(
            and_(
                ChatwootLegacyMap.resource_type == ChatwootMapResourceType.TEAM,
                ChatwootLegacyMap.tenant_id == tenant_id,
            )
        )
    )
    return {int(r.chatwoot_id): r.local_uuid for r in q.all()}


async def _redact_metric_item_ids(
    db: AsyncSession, tenant_id: UUID, data: Any, *, kind: str
) -> Any:
    """Thay id số Chatwoot (agent/team) bằng UUID nội bộ trong metrics."""
    if kind == "team":
        cw_map = await _chatwoot_team_id_to_local_map(db, tenant_id)
    else:
        cw_map = await _chatwoot_agent_id_to_local_map(db, tenant_id)

    def fix(item: Any) -> Any:
        if not isinstance(item, dict):
            return item
        out = dict(item)
        raw_id = out.get("id")
        if isinstance(raw_id, int) and raw_id in cw_map:
            out["id"] = str(cw_map[raw_id])
        return out

    if isinstance(data, list):
        return [fix(x) for x in data]
    return fix(data)


async def _translate_agent_scope_id(
    db: AsyncSession, tenant_id: UUID, report_type: str, scope_id: Optional[str]
) -> tuple[Optional[str], Optional[Any]]:
    """
    FE truyền UUID nội bộ → dịch sang id số Chatwoot:
    - type=agent: map AGENT theo tenant
    - type=team:  map TEAM theo tenant
    Type khác (inbox/label) giữ nguyên id FE gửi.
    """
    if not scope_id or report_type not in ("agent", "team"):
        return scope_id, None
    try:
        local_uuid = UUID(scope_id)
    except ValueError:
        return scope_id, None  # đã là id số / label title

    if report_type == "team":
        row = await _map_tenant_team_by_local(db, tenant_id, local_uuid)
        not_found_msg = "Không tìm thấy team map với UUID này trong tenant"
    else:
        row = await _map_tenant_agent_by_local(db, tenant_id, local_uuid)
        not_found_msg = "Không tìm thấy agent map với UUID này trong tenant"

    if not row:
        return None, api_response(
            ResponseStatus.ERROR,
            ResponseStatusCode.NOT_FOUND,
            not_found_msg,
        )
    return str(row.chatwoot_id), None


# ---------------------------------------------------------------------------
# 1) Timeseries: GET /api/v2/accounts/{id}/reports
# ---------------------------------------------------------------------------
async def get_report_timeseries(
    request: Request,
    current_user: User,
    tenant_id: UUID,
    db: AsyncSession,
    *,
    metric: str,
    report_type: str = "account",
    scope_id: Optional[str] = None,
    since: Optional[str] = None,
    until: Optional[str] = None,
    group_by: Optional[str] = None,
    business_hours: Optional[bool] = None,
):
    metric = (metric or "").strip()
    report_type = (report_type or "account").strip().lower()

    if metric not in REPORT_METRICS:
        return _bad_request(
            f"metric không hợp lệ. Hợp lệ: {', '.join(sorted(REPORT_METRICS))}"
        )
    if report_type not in REPORT_TYPES:
        return _bad_request(
            f"type không hợp lệ. Hợp lệ: {', '.join(sorted(REPORT_TYPES))}"
        )
    if group_by and group_by not in REPORT_GROUP_BY:
        return _bad_request(
            f"group_by không hợp lệ. Hợp lệ: {', '.join(sorted(REPORT_GROUP_BY))}"
        )

    scope_id, err = await _translate_agent_scope_id(db, tenant_id, report_type, scope_id)
    if err is not None:
        return err

    params: list[tuple[str, str]] = [("metric", metric), ("type", report_type)]
    if scope_id:
        params.append(("id", scope_id))
    if since:
        params.append(("since", _to_epoch_str(since)))
    if until:
        params.append(("until", _to_epoch_str(until)))
    if group_by:
        params.append(("group_by", group_by))
    if business_hours is not None:
        params.append(("business_hours", "true" if business_hours else "false"))

    return await _forward_report(
        current_user,
        tenant_id,
        db,
        path_builder=lambda aid: f"/api/v2/accounts/{aid}/reports",
        params=params,
        ok_message="Lấy báo cáo timeseries thành công",
    )


# ---------------------------------------------------------------------------
# 2) Summary: GET /api/v2/accounts/{id}/reports/summary
# ---------------------------------------------------------------------------
async def get_report_summary(
    request: Request,
    current_user: User,
    tenant_id: UUID,
    db: AsyncSession,
    *,
    report_type: str = "account",
    scope_id: Optional[str] = None,
    since: Optional[str] = None,
    until: Optional[str] = None,
    business_hours: Optional[bool] = None,
):
    report_type = (report_type or "account").strip().lower()
    if report_type not in REPORT_TYPES:
        return _bad_request(
            f"type không hợp lệ. Hợp lệ: {', '.join(sorted(REPORT_TYPES))}"
        )

    scope_id, err = await _translate_agent_scope_id(db, tenant_id, report_type, scope_id)
    if err is not None:
        return err

    params: list[tuple[str, str]] = [("type", report_type)]
    if scope_id:
        params.append(("id", scope_id))
    if since:
        params.append(("since", _to_epoch_str(since)))
    if until:
        params.append(("until", _to_epoch_str(until)))
    if business_hours is not None:
        params.append(("business_hours", "true" if business_hours else "false"))

    return await _forward_report(
        current_user,
        tenant_id,
        db,
        path_builder=lambda aid: f"/api/v2/accounts/{aid}/reports/summary",
        params=params,
        ok_message="Lấy báo cáo tổng hợp thành công",
    )


# ---------------------------------------------------------------------------
# 3) Realtime conversation metrics (account): open / unattended / unassigned
# ---------------------------------------------------------------------------
async def get_conversation_metrics_account(
    request: Request,
    current_user: User,
    tenant_id: UUID,
    db: AsyncSession,
):
    return await _forward_report(
        current_user,
        tenant_id,
        db,
        path_builder=lambda aid: f"/api/v2/accounts/{aid}/reports/conversations",
        params=[("type", "account")],
        ok_message="Lấy metrics hội thoại (account) thành công",
    )


# ---------------------------------------------------------------------------
# 4) Conversation metrics theo agent
# ---------------------------------------------------------------------------
async def get_conversation_metrics_agents(
    request: Request,
    current_user: User,
    tenant_id: UUID,
    db: AsyncSession,
    *,
    agent_id: Optional[str] = None,
):
    params: list[tuple[str, str]] = [("type", "agent")]

    remote_id, err = await _translate_agent_scope_id(db, tenant_id, "agent", agent_id)
    if err is not None:
        return err
    if remote_id:
        params.append(("user_id", remote_id))

    return await _forward_report(
        current_user,
        tenant_id,
        db,
        path_builder=lambda aid: f"/api/v2/accounts/{aid}/reports/conversations/",
        params=params,
        ok_message="Lấy metrics hội thoại theo agent thành công",
        redact_items="agent",
    )


# ---------------------------------------------------------------------------
# 5) Conversation traffic theo giờ (heatmap)
# ---------------------------------------------------------------------------
async def get_conversation_traffic(
    request: Request,
    current_user: User,
    tenant_id: UUID,
    db: AsyncSession,
    *,
    timezone_offset: Optional[str] = None,
    since: Optional[str] = None,
    until: Optional[str] = None,
):
    params: list[tuple[str, str]] = []
    if timezone_offset:
        params.append(("timezone_offset", timezone_offset))
    if since:
        params.append(("since", _to_epoch_str(since)))
    if until:
        params.append(("until", _to_epoch_str(until)))

    return await _forward_report(
        current_user,
        tenant_id,
        db,
        path_builder=lambda aid: f"/api/v2/accounts/{aid}/reports/conversation_traffic",
        params=params,
        ok_message="Lấy conversation traffic thành công",
    )


# ---------------------------------------------------------------------------
# 6) Summary reports theo agent/team/label/channel (Chatwoot >= 4.10)
# ---------------------------------------------------------------------------
async def get_grouped_summary_report(
    request: Request,
    current_user: User,
    tenant_id: UUID,
    db: AsyncSession,
    *,
    kind: str,
    since: Optional[str] = None,
    until: Optional[str] = None,
    business_hours: Optional[bool] = None,
):
    kind = (kind or "").strip().lower()
    if kind not in SUMMARY_REPORT_KINDS:
        return _bad_request(
            f"kind không hợp lệ. Hợp lệ: {', '.join(sorted(SUMMARY_REPORT_KINDS))}"
        )

    params: list[tuple[str, str]] = []
    if since:
        params.append(("since", _to_epoch_str(since)))
    if until:
        params.append(("until", _to_epoch_str(until)))
    if business_hours is not None:
        params.append(("business_hours", "true" if business_hours else "false"))

    return await _forward_report(
        current_user,
        tenant_id,
        db,
        path_builder=lambda aid: f"/api/v2/accounts/{aid}/summary_reports/{kind}",
        params=params,
        ok_message=f"Lấy summary report theo {kind} thành công",
        redact_items=kind if kind in ("agent", "team") else None,
    )


# ---------------------------------------------------------------------------
# 7) CSAT metrics + danh sách phản hồi
# ---------------------------------------------------------------------------
async def get_csat_metrics(
    request: Request,
    current_user: User,
    tenant_id: UUID,
    db: AsyncSession,
    *,
    since: Optional[str] = None,
    until: Optional[str] = None,
    agent_id: Optional[str] = None,
):
    params: list[tuple[str, str]] = []
    if since:
        params.append(("since", _to_epoch_str(since)))
    if until:
        params.append(("until", _to_epoch_str(until)))

    remote_id, err = await _translate_agent_scope_id(db, tenant_id, "agent", agent_id)
    if err is not None:
        return err
    if remote_id:
        params.append(("user_ids[]", remote_id))

    return await _forward_report(
        current_user,
        tenant_id,
        db,
        path_builder=lambda aid: f"/api/v1/accounts/{aid}/csat_survey_responses/metrics",
        params=params,
        ok_message="Lấy CSAT metrics thành công",
    )


async def list_csat_responses(
    request: Request,
    current_user: User,
    tenant_id: UUID,
    db: AsyncSession,
    *,
    page: int = 1,
    since: Optional[str] = None,
    until: Optional[str] = None,
    agent_id: Optional[str] = None,
):
    params: list[tuple[str, str]] = [("page", str(page))]
    if since:
        params.append(("since", _to_epoch_str(since)))
    if until:
        params.append(("until", _to_epoch_str(until)))

    remote_id, err = await _translate_agent_scope_id(db, tenant_id, "agent", agent_id)
    if err is not None:
        return err
    if remote_id:
        params.append(("user_ids[]", remote_id))

    return await _forward_report(
        current_user,
        tenant_id,
        db,
        path_builder=lambda aid: f"/api/v1/accounts/{aid}/csat_survey_responses",
        params=params,
        ok_message="Lấy danh sách CSAT responses thành công",
    )


# ---------------------------------------------------------------------------
# 8) Overview tổng hợp cho dashboard (gộp nhiều call thành 1 response)
# ---------------------------------------------------------------------------
async def get_dashboard_overview(
    request: Request,
    current_user: User,
    tenant_id: UUID,
    db: AsyncSession,
    *,
    since: Optional[str] = None,
    until: Optional[str] = None,
):
    """
    Gộp: summary (kỳ + kỳ trước) + realtime conversation metrics + CSAT metrics.
    Tiện cho FE render dashboard bằng 1 request.
    """
    try:
        denied = await _require_tenant_access(current_user, tenant_id, db)
        if denied is not None:
            return denied

        account_id, _ = await _resolve_account_id(db, tenant_id)
        if account_id is None:
            return api_response(
                ResponseStatus.ERROR,
                ResponseStatusCode.NOT_FOUND,
                "Chưa có map messaging account cho tenant này",
            )

        period: list[tuple[str, str]] = []
        if since:
            period.append(("since", _to_epoch_str(since)))
        if until:
            period.append(("until", _to_epoch_str(until)))

        summary_res = await chatwoot_client.application_request(
            "GET",
            f"/api/v2/accounts/{account_id}/reports/summary",
            params=[("type", "account"), *period],
        )
        live_res = await chatwoot_client.application_request(
            "GET",
            f"/api/v2/accounts/{account_id}/reports/conversations",
            params=[("type", "account")],
        )
        csat_res = await chatwoot_client.application_request(
            "GET",
            f"/api/v1/accounts/{account_id}/csat_survey_responses/metrics",
            params=period or None,
        )

        def block(res) -> dict[str, Any]:
            if res.status_code == 200:
                return {"ok": True, "data": res.data}
            return {
                "ok": False,
                "error": _chatwoot_error_payload(res),
            }

        return api_response(
            ResponseStatus.SUCCESS,
            ResponseStatusCode.OK,
            "Lấy dashboard overview thành công",
            {
                "tenant_id": str(tenant_id),
                "summary": block(summary_res),
                "live_conversations": block(live_res),
                "csat": block(csat_res),
            },
        )
    except SQLAlchemyError as e:
        return api_response(
            ResponseStatus.ERROR,
            ResponseStatusCode.INTERNAL_SERVER_ERROR,
            f"Lỗi CSDL: {e}",
        )
    except Exception as e:
        logger.error("[chatwoot reports overview] %s", e)
        return api_response(
            ResponseStatus.ERROR,
            ResponseStatusCode.INTERNAL_SERVER_ERROR,
            f"Lỗi không xác định: {e}",
        )
