"""
Chuẩn hóa payload POST/PATCH account Platform API.

Chatwoot có thể trả 500 sau khi đã tạo bản ghi nếu `features` chứa key không tồn tại;
retry POST sẽ tạo **duplicate account** — không được tự gọi lại.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

# Các flag feature hợp lệ (Chatwoot ~4.x — lấy từ response account + mã nguồn công khai).
# Key không thuộc whitelist sẽ bị **bỏ** trước khi gửi (có log + trả về meta cho client).
CHATWOOT_ACCOUNT_FEATURE_FLAG_KEYS_KNOWN = frozenset(
    {
        "inbound_emails",
        "channel_email",
        "channel_facebook",
        "channel_twitter",
        "channel_instagram",
        "channel_tiktok",
        "channel_website",
        "channel_voice",
        "help_center",
        "agent_bots",
        "macros",
        "agent_management",
        "team_management",
        "inbox_management",
        "labels",
        "automations",
        "canned_responses",
        "integrations",
        "voice_recorder",
        "campaigns",
        "reports",
        "crm",
        "auto_resolve_conversations",
        "chatwoot_v4",
        "report_v4",
        "contact_chatwoot_support_team",
        # Trong JSON `features`, có entry trùng tên khái niệm "custom_attributes" (feature), khác object top-level
        "custom_attributes",
        "disable_branding",
        "sla",
        "help_center_embedding_search",
        "captain_integration",
        "advanced_search",
    }
)


def sanitize_platform_account_payload(
    payload: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """
    - Lọc `features` theo whitelist (unknown keys → bỏ, không gửi sang Chatwoot).
    - Bỏ `limits` / `custom_attributes` nếu là dict rỗng (tránh edge case Rails).
    - Nếu `features` rỗng sau lọc → xóa key (không gửi {}).
    Trả về (payload_sạch, meta cho API response / log).
    """
    out: dict[str, Any] = dict(payload)
    meta: dict[str, Any] = {}

    if "features" in out and isinstance(out["features"], dict):
        raw_feats: dict[str, Any] = out["features"]
        stripped: list[str] = []
        clean: dict[str, Any] = {}
        for k, v in raw_feats.items():
            if k in CHATWOOT_ACCOUNT_FEATURE_FLAG_KEYS_KNOWN:
                clean[k] = v
            else:
                stripped.append(k)
        if stripped:
            meta["stripped_invalid_feature_keys"] = stripped
            logger.warning(
                "Chatwoot features: bỏ key không hợp lệ (không retry POST): %s",
                stripped,
            )
        if not clean:
            del out["features"]
            meta["features_omitted_empty_after_sanitize"] = True
        else:
            out["features"] = clean

    if out.get("limits") == {}:
        del out["limits"]
        meta["omitted_empty_limits"] = True

    if out.get("custom_attributes") == {}:
        del out["custom_attributes"]
        meta["omitted_empty_custom_attributes"] = True

    return out, meta
