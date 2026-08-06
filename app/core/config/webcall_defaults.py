"""Default configs shared across models / services."""

from __future__ import annotations

from copy import deepcopy
from typing import Any


DEFAULT_WEBCALL_CONFIG: dict[str, Any] = {
    "enable_widget": True,
    "sip_only": True,
    "sip_domain": "",
    "domain_uuid": "",
    "hotlines": [],
    "ws_server": "",
    "sip_password": "",
    "api_key": "",
    "extension": "",
    "webhook_secret": "",
}


def merge_webcall_config(existing: dict[str, Any] | None = None) -> dict[str, Any]:
    """
    Merge với default: giữ giá trị tenant đã cấu hình, bổ sung key còn thiếu.
    """
    merged = deepcopy(DEFAULT_WEBCALL_CONFIG)
    if isinstance(existing, dict):
        for key, value in existing.items():
            if value is None:
                continue
            # Giữ giá trị đã set (kể cả "" / [] nếu user cố ý)
            if key in merged:
                merged[key] = value
            else:
                # giữ key custom ngoài default
                merged[key] = value
    return merged
