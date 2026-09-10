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

Phạm vi:
- admin-partner / platform: full account tenant (partner dùng token cá nhân khi đã là Chatwoot administrator)
- agent thường: chỉ metric cá nhân (type=agent) + inbox mình là member (Reports API gọi bằng env admin + OmniHub clamp)
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional
from uuid import UUID

from fastapi import Request
from sqlalchemy import and_, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ChatwootLegacyMap, ChatwootMapResourceType, User
from app.integrations.chatwoot import client as chatwoot_client
from app.schemas.responses.api_response_rule import (
    ResponseStatus,
    ResponseStatusCode,
    api_response,
)
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
REPORT_GROUP_BY = frozenset({"day", "week", "month", "year", "hour"})
SUMMARY_REPORT_KINDS = frozenset({"agent", "team", "label", "channel", "inbox"})


@dataclass
class _ReportCallerScope:
    elevated: bool
    token: str
    chatwoot_user_id: int | None
    inbox_ids: set[int]


def _to_epoch_str(value: Optional[str]) -> Optional[str]:
    """Chatwoot nhận since/until unix epoch; hỗ trợ ISO date/datetime."""
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
        return v


def _bad_request(message: str) -> Any:
    return api_response(
        ResponseStatus.ERROR,
        ResponseStatusCode.BAD_REQUEST,
        message,
    )


def _forbidden_scope(message: str, *, code: str = "messaging_reports_scope") -> Any:
    return api_response(
        ResponseStatus.ERROR,
        ResponseStatusCode.FORBIDDEN,
        message,
        {"code": code},
    )


async def _load_report_caller_scope(
    current_user: User,
    tenant_id: UUID,
    db: AsyncSession,
) -> tuple[_ReportCallerScope | None, Any]:
    from app.services.v1.handle_chatwoot.user_tokens import (
        ensure_user_chatwoot_api_token,
        fetch_member_inbox_ids,
        resolve_chatwoot_user_id,
        resolve_reports_access_token,
        user_is_elevated_messaging_admin,
    )

    denied = await _require_tenant_access(current_user, tenant_id, db)
    if denied is not None:
        return None, denied

    token, tok_err = await resolve_reports_access_token(db, current_user)
    if tok_err is not None:
        return None, tok_err

    elevated = await user_is_elevated_messaging_admin(db, current_user)
    if elevated:
        return _ReportCallerScope(True, token, None, set()), None

    cw_id = await resolve_chatwoot_user_id(db, current_user)
    personal = await ensure_user_chatwoot_api_token(db, current_user)
    if cw_id is None or not personal:
        return None, _forbidden_scope(
            "Tài khoản chưa sẵn sàng xem báo cáo. Vui lòng liên hệ quản trị viên.",
            code="chatwoot_user_map_required",
        )
    inbox_ids = await fetch_member_inbox_ids(
        db, current_user, tenant_id, personal_token=personal
    )
    return _ReportCallerScope(False, token, int(cw_id), inbox_ids), None


def _clamp_report_type_scope(
    scope: _ReportCallerScope,
    report_type: str,
    scope_id: Optional[str],
) -> tuple[Optional[str], Optional[str], Any]:
    """Agent: ép cá nhân / inbox member; cấm team/label/account full."""
    if scope.elevated:
        return report_type, scope_id, None

    self_id = str(scope.chatwoot_user_id)
    if report_type in ("account", "agent"):
        if scope_id and str(scope_id) != self_id:
            return None, None, _forbidden_scope(
                "Bạn chỉ xem được báo cáo của chính mình."
            )
        return "agent", self_id, None

    if report_type == "inbox":
        if not scope_id:
            return "agent", self_id, None
        try:
            iid = int(scope_id)
        except (TypeError, ValueError):
            return None, None, _bad_request("Hộp thư được chọn không hợp lệ.")
        if iid not in scope.inbox_ids:
            return None, None, _forbidden_scope(
                "Bạn không có quyền xem hộp thư này."
            )
        return "inbox", str(iid), None

    return None, None, _forbidden_scope(
        "Bạn chỉ xem được báo cáo cá nhân hoặc hộp thư mình tham gia."
    )


def _filter_metric_items_by_ids(data: Any, allowed: set[int]) -> Any:
    def keep(item: Any) -> bool:
        if not isinstance(item, dict):
            return False
        try:
            return int(item.get("id")) in allowed
        except (TypeError, ValueError):
            return False

    if isinstance(data, list):
        return [x for x in data if keep(x)]
    if isinstance(data, dict) and isinstance(data.get("payload"), list):
        out = dict(data)
        out["payload"] = [x for x in data["payload"] if keep(x)]
        return out
    return data


async def _forward_report(
    current_user: User,
    tenant_id: UUID,
    db: AsyncSession,
    *,
    path_builder,
    params: list[tuple[str, str]],
    ok_message: str,
    redact_items: Optional[str] = None,
    caller_scope: _ReportCallerScope | None = None,
    filter_item_ids: Optional[set[int]] = None,
) -> Any:
    """
    Forward báo cáo tới Chatwoot. Dùng admin Application token (Reports cần
    Administrator); phạm vi agent đã clamp ở caller.
    """
    try:
        scope = caller_scope
        if scope is None:
            scope, err = await _load_report_caller_scope(current_user, tenant_id, db)
            if err is not None:
                return err
        assert scope is not None

        account_id, _ = await _resolve_account_id(db, tenant_id)
        if account_id is None:
            return api_response(
                ResponseStatus.ERROR,
                ResponseStatusCode.NOT_FOUND,
                "Doanh nghiệp chưa được liên kết kênh trò chuyện.",
            )

        path = path_builder(account_id)
        res = await chatwoot_client.application_request(
            "GET", path, params=params, access_token=scope.token
        )

        if res.status_code == 200:
            data: Any = res.data
            if filter_item_ids is not None:
                data = _filter_metric_items_by_ids(data, filter_item_ids)
            if redact_items:
                data = await _redact_metric_item_ids(
                    db, tenant_id, data, kind=redact_items
                )
            return api_response(
                ResponseStatus.SUCCESS,
                ResponseStatusCode.OK,
                ok_message,
                {
                    "tenant_id": str(tenant_id),
                    "messaging": data,
                    "scope": (
                        "tenant" if scope.elevated else "personal_or_member_inbox"
                    ),
                },
            )

        return api_response(
            ResponseStatus.ERROR,
            _application_error_http_status(res.status_code),
            "Không lấy được báo cáo. Vui lòng thử lại sau.",
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
        return scope_id, None

    if report_type == "team":
        row = await _map_tenant_team_by_local(db, tenant_id, local_uuid)
        not_found_msg = "Không tìm thấy nhóm trong doanh nghiệp này."
    else:
        row = await _map_tenant_agent_by_local(db, tenant_id, local_uuid)
        not_found_msg = "Không tìm thấy nhân viên trong doanh nghiệp này."

    if not row:
        return None, api_response(
            ResponseStatus.ERROR,
            ResponseStatusCode.NOT_FOUND,
            not_found_msg,
        )
    return str(row.chatwoot_id), None


# ---------------------------------------------------------------------------
# 1) Timeseries
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

    scope, scope_err = await _load_report_caller_scope(current_user, tenant_id, db)
    if scope_err is not None:
        return scope_err
    assert scope is not None

    scope_id, err = await _translate_agent_scope_id(db, tenant_id, report_type, scope_id)
    if err is not None:
        return err

    report_type, scope_id, clamp_err = _clamp_report_type_scope(
        scope, report_type, scope_id
    )
    if clamp_err is not None:
        return clamp_err

    params: list[tuple[str, str]] = [("metric", metric), ("type", report_type or "agent")]
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
        caller_scope=scope,
    )


# ---------------------------------------------------------------------------
# 2) Summary
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

    scope, scope_err = await _load_report_caller_scope(current_user, tenant_id, db)
    if scope_err is not None:
        return scope_err
    assert scope is not None

    scope_id, err = await _translate_agent_scope_id(db, tenant_id, report_type, scope_id)
    if err is not None:
        return err

    report_type, scope_id, clamp_err = _clamp_report_type_scope(
        scope, report_type, scope_id
    )
    if clamp_err is not None:
        return clamp_err

    params: list[tuple[str, str]] = [("type", report_type or "agent")]
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
        caller_scope=scope,
    )


# ---------------------------------------------------------------------------
# 3) Realtime conversation metrics (account) — agent → cá nhân
# ---------------------------------------------------------------------------
async def get_conversation_metrics_account(
    request: Request,
    current_user: User,
    tenant_id: UUID,
    db: AsyncSession,
):
    scope, scope_err = await _load_report_caller_scope(current_user, tenant_id, db)
    if scope_err is not None:
        return scope_err
    assert scope is not None

    if scope.elevated:
        params: list[tuple[str, str]] = [("type", "account")]
    else:
        params = [
            ("type", "agent"),
            ("user_id", str(scope.chatwoot_user_id)),
        ]

    return await _forward_report(
        current_user,
        tenant_id,
        db,
        path_builder=lambda aid: (
            f"/api/v2/accounts/{aid}/reports/conversations"
            if scope.elevated
            else f"/api/v2/accounts/{aid}/reports/conversations/"
        ),
        params=params,
        ok_message="Lấy metrics hội thoại thành công",
        caller_scope=scope,
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
    scope, scope_err = await _load_report_caller_scope(current_user, tenant_id, db)
    if scope_err is not None:
        return scope_err
    assert scope is not None

    params: list[tuple[str, str]] = [("type", "agent")]
    filter_ids: Optional[set[int]] = None

    remote_id, err = await _translate_agent_scope_id(db, tenant_id, "agent", agent_id)
    if err is not None:
        return err

    if not scope.elevated:
        self_id = str(scope.chatwoot_user_id)
        if remote_id and remote_id != self_id:
            return _forbidden_scope("Bạn chỉ xem được số liệu hội thoại của chính mình.")
        remote_id = self_id
        filter_ids = {int(scope.chatwoot_user_id)}

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
        caller_scope=scope,
        filter_item_ids=filter_ids,
    )


# ---------------------------------------------------------------------------
# 5) Conversation traffic — chỉ elevated (account-wide)
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
    scope, scope_err = await _load_report_caller_scope(current_user, tenant_id, db)
    if scope_err is not None:
        return scope_err
    assert scope is not None

    if not scope.elevated:
        return _forbidden_scope(
            "Biểu đồ lưu lượng hội thoại chỉ dành cho quản trị viên."
        )

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
        caller_scope=scope,
    )


# ---------------------------------------------------------------------------
# 6) Summary reports theo agent/team/label/channel
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

    scope, scope_err = await _load_report_caller_scope(current_user, tenant_id, db)
    if scope_err is not None:
        return scope_err
    assert scope is not None

    filter_ids: Optional[set[int]] = None
    if not scope.elevated:
        if kind == "agent":
            filter_ids = {int(scope.chatwoot_user_id)}
        elif kind == "inbox":
            if not scope.inbox_ids:
                return api_response(
                    ResponseStatus.SUCCESS,
                    ResponseStatusCode.OK,
                    f"Lấy summary report theo {kind} thành công",
                    {
                        "tenant_id": str(tenant_id),
                        "messaging": [],
                        "scope": "personal_or_member_inbox",
                    },
                )
            filter_ids = set(scope.inbox_ids)
        else:
            return _forbidden_scope(
                "Bạn chỉ xem được báo cáo theo nhân viên (chính mình) hoặc hộp thư mình tham gia."
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
        caller_scope=scope,
        filter_item_ids=filter_ids,
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
    scope, scope_err = await _load_report_caller_scope(current_user, tenant_id, db)
    if scope_err is not None:
        return scope_err
    assert scope is not None

    params: list[tuple[str, str]] = []
    if since:
        params.append(("since", _to_epoch_str(since)))
    if until:
        params.append(("until", _to_epoch_str(until)))

    remote_id, err = await _translate_agent_scope_id(db, tenant_id, "agent", agent_id)
    if err is not None:
        return err

    if not scope.elevated:
        self_id = str(scope.chatwoot_user_id)
        if remote_id and remote_id != self_id:
            return _forbidden_scope("Bạn chỉ xem được đánh giá của chính mình.")
        remote_id = self_id

    if remote_id:
        params.append(("user_ids[]", remote_id))

    return await _forward_report(
        current_user,
        tenant_id,
        db,
        path_builder=lambda aid: f"/api/v1/accounts/{aid}/csat_survey_responses/metrics",
        params=params,
        ok_message="Lấy CSAT metrics thành công",
        caller_scope=scope,
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
    scope, scope_err = await _load_report_caller_scope(current_user, tenant_id, db)
    if scope_err is not None:
        return scope_err
    assert scope is not None

    params: list[tuple[str, str]] = [("page", str(page))]
    if since:
        params.append(("since", _to_epoch_str(since)))
    if until:
        params.append(("until", _to_epoch_str(until)))

    remote_id, err = await _translate_agent_scope_id(db, tenant_id, "agent", agent_id)
    if err is not None:
        return err

    if not scope.elevated:
        self_id = str(scope.chatwoot_user_id)
        if remote_id and remote_id != self_id:
            return _forbidden_scope("Bạn chỉ xem được đánh giá của chính mình.")
        remote_id = self_id

    if remote_id:
        params.append(("user_ids[]", remote_id))

    return await _forward_report(
        current_user,
        tenant_id,
        db,
        path_builder=lambda aid: f"/api/v1/accounts/{aid}/csat_survey_responses",
        params=params,
        ok_message="Lấy danh sách CSAT responses thành công",
        caller_scope=scope,
    )


# ---------------------------------------------------------------------------
# 8) Overview dashboard
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
    Gộp: summary + realtime conversation metrics + CSAT metrics.
    Agent: chỉ dữ liệu cá nhân; admin-partner: full tenant.
    """
    try:
        scope, scope_err = await _load_report_caller_scope(current_user, tenant_id, db)
        if scope_err is not None:
            return scope_err
        assert scope is not None

        account_id, _ = await _resolve_account_id(db, tenant_id)
        if account_id is None:
            return api_response(
                ResponseStatus.ERROR,
                ResponseStatusCode.NOT_FOUND,
                "Doanh nghiệp chưa được liên kết kênh trò chuyện.",
            )

        period: list[tuple[str, str]] = []
        if since:
            period.append(("since", _to_epoch_str(since)))
        if until:
            period.append(("until", _to_epoch_str(until)))

        if scope.elevated:
            summary_params: list[tuple[str, str]] = [("type", "account"), *period]
            live_params: list[tuple[str, str]] = [("type", "account")]
            live_path = f"/api/v2/accounts/{account_id}/reports/conversations"
            csat_params: list[tuple[str, str]] = list(period)
        else:
            self_id = str(scope.chatwoot_user_id)
            summary_params = [("type", "agent"), ("id", self_id), *period]
            live_params = [("type", "agent"), ("user_id", self_id)]
            live_path = f"/api/v2/accounts/{account_id}/reports/conversations/"
            csat_params = [*period, ("user_ids[]", self_id)]

        summary_res = await chatwoot_client.application_request(
            "GET",
            f"/api/v2/accounts/{account_id}/reports/summary",
            params=summary_params,
            access_token=scope.token,
        )
        live_res = await chatwoot_client.application_request(
            "GET",
            live_path,
            params=live_params,
            access_token=scope.token,
        )
        csat_res = await chatwoot_client.application_request(
            "GET",
            f"/api/v1/accounts/{account_id}/csat_survey_responses/metrics",
            params=csat_params or None,
            access_token=scope.token,
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
                "scope": "tenant" if scope.elevated else "personal_or_member_inbox",
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
