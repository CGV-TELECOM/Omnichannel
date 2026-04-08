"""HTTP client gọi Chatwoot Platform API và Application API."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import httpx

from app.core.config.app_config import settings

logger = logging.getLogger(__name__)

_RAW_PREVIEW_LOG = 3500


@dataclass(frozen=True, slots=True)
class ChatwootResult:
    """Kết quả HTTP từ Chatwoot (luôn giữ body thô để debug Rails/nginx)."""

    status_code: int
    data: Any
    raw_text: str
    path: str


def _base_url() -> str:
    raw = (settings.CHATWOOT_BASE_URL or "").strip().rstrip("/")
    return raw


async def _request(
    method: str,
    url: str,
    *,
    path: str,
    token: str | None,
    json_body: dict[str, Any] | None = None,
) -> ChatwootResult:
    if not token:
        return ChatwootResult(
            401,
            {"description": "Thiếu api_access_token Chatwoot"},
            "",
            path,
        )
    headers = {
        # "api_access_token": token,
        "api-access-token": token,
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    try:
        async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as client:
            r = await client.request(method, url, headers=headers, json=json_body)
    except UnicodeEncodeError as exc:
        return ChatwootResult(
            500,
            {
                "description": (
                    "CHATWOOT_BASE_URL hoặc token chứa ký tự không hợp lệ cho HTTP "
                    "(thường do copy dấu gạch đặc biệt vào .env)."
                ),
                "detail": str(exc),
            },
            "",
            path,
        )

    raw = r.text or ""
    data: Any
    if r.content:
        try:
            data = r.json()
        except Exception:
            data = raw
    else:
        data = None

    if r.status_code >= 400:
        logger.warning(
            "Chatwoot %s %s HTTP %s | response preview: %s",
            method,
            path,
            r.status_code,
            raw[:_RAW_PREVIEW_LOG].replace("\n", " ") if raw else "(empty)",
        )

    return ChatwootResult(r.status_code, data, raw, path)


async def platform_request(
    method: str,
    path: str,
    *,
    json_body: dict[str, Any] | None = None,
) -> ChatwootResult:
    base = _base_url()
    token = settings.CHATWOOT_PLATFORM_API_TOKEN
    if not base:
        return ChatwootResult(
            503, {"description": "CHATWOOT_BASE_URL chưa cấu hình"}, "", ""
        )
    path_norm = path if path.startswith("/") else f"/{path}"
    return await _request(
        method,
        f"{base}{path_norm}",
        path=path_norm,
        token=token,
        json_body=json_body,
    )


async def application_request(
    method: str,
    path: str,
    *,
    json_body: dict[str, Any] | None = None,
) -> ChatwootResult:
    base = _base_url()
    token = settings.CHATWOOT_USER_API_TOKEN
    if not base:
        return ChatwootResult(
            503, {"description": "CHATWOOT_BASE_URL chưa cấu hình"}, "", ""
        )
    path_norm = path if path.startswith("/") else f"/{path}"
    return await _request(
        method,
        f"{base}{path_norm}",
        path=path_norm,
        token=token,
        json_body=json_body,
    )
