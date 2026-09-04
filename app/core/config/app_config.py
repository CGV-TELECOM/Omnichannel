import os
from dotenv import load_dotenv

load_dotenv()


def _sanitize_chatwoot_env(value: str | None) -> str | None:
    """
    URL và header api_access_token phải dùng ký tự ASCII/latin-1.
    Copy từ Word/PDF thường dính gạch —, – hoặc khoảng trắng đặc biệt → lỗi encode khi gọi HTTP.
    """
    if value is None:
        return None
    s = value.strip()
    for bad, good in (
        ("\u2014", "-"),  # em dash —
        ("\u2013", "-"),  # en dash –
        ("\u2212", "-"),  # minus sign
        ("\u2012", "-"),  # figure dash
        ("\u2015", "-"),  # horizontal bar
        ("\uff0d", "-"),  # fullwidth hyphen-minus
        ("\u00a0", " "),  # nbsp
    ):
        s = s.replace(bad, good)
    return s


def _normalize_redis_url(raw: str | None) -> str:
    """Đảm bảo REDIS_URL có scheme redis:// | rediss:// | unix://."""
    s = (raw or "").strip().strip('"').strip("'")
    if not s:
        return "redis://127.0.0.1:6379"
    if "://" not in s:
        return f"redis://{s}"
    return s


def _optional_positive_int_env(var_name: str) -> int | None:
    """Số nguyên dương từ .env; rỗng / không hợp lệ → None."""
    raw = os.getenv(var_name)
    if raw is None:
        return None
    s = str(raw).strip()
    if not s:
        return None
    try:
        n = int(s)
    except ValueError:
        return None
    return n if n > 0 else None


class Settings:
    PROJECT_NAME: str = os.getenv("PROJECT_NAME")
    PROJECT_VERSION: str = os.getenv("PROJECT_VERSION")
    DATABASE_URL: str = os.getenv("DATABASE_URL")   
    RATE_LIMIT_WINDOW: int = os.getenv("RATE_LIMIT_WINDOW")
    RATE_LIMIT_LIMIT: int = os.getenv("RATE_LIMIT_LIMIT")
    REDIS_URL: str = _normalize_redis_url(os.getenv("REDIS_URL"))
    # Timezone Configuration - Default: Asia/Ho_Chi_Minh (UTC+7)
    TIMEZONE: str = os.getenv("TIMEZONE", "Asia/Ho_Chi_Minh")
    # JWT
    ALGORITHM: str = os.getenv("ALGORITHM")
    SECRET_KEY: str = os.getenv("SECRET_KEY")
    REFRESH_TOKEN_EXPIRE_DAYS: int = int(os.getenv("REFRESH_TOKEN_EXPIRE_DAYS"))
    ACCESS_TOKEN_EXPIRE_MINUTES: int = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES"))
    # Email 
    SMTP_HOST: str = os.getenv("SMTP_HOST")
    SMTP_PORT: int = int(os.getenv("SMTP_PORT"))
    SMTP_USERNAME: str = os.getenv("SMTP_USERNAME")
    SMTP_PASSWORD: str = os.getenv("SMTP_PASSWORD")

    # Chatwoot (Platform API: tài khoản; Application API: agent — cần user api_access_token)
    CHATWOOT_BASE_URL: str | None = _sanitize_chatwoot_env(os.getenv("CHATWOOT_BASE_URL"))
    CHATWOOT_PLATFORM_API_TOKEN: str | None = _sanitize_chatwoot_env(
        os.getenv("CHATWOOT_PLATFORM_API_TOKEN")
    )
    CHATWOOT_USER_API_TOKEN: str | None = _sanitize_chatwoot_env(
        os.getenv("CHATWOOT_USER_API_TOKEN")
    )
    # Token user AI Bot (vd Techie A.I) — dùng khi OmniHub gửi tin KG/picker.
    # Chatwoot gắn sender = chủ api_access_token; thiếu thì fallback CHATWOOT_USER_API_TOKEN
    # (tin sẽ hiện tên admin tích hợp, vd BerlinHoang).
    CHATWOOT_BOT_API_TOKEN: str | None = _sanitize_chatwoot_env(
        os.getenv("CHATWOOT_BOT_API_TOKEN")
    )
    # Tùy chọn: id user Chatwoot (số) tương ứng CHATWOOT_USER_API_TOKEN — tránh gọi GET /api/v1/profile
    CHATWOOT_INTEGRATION_USER_ID: int | None = _optional_positive_int_env(
        "CHATWOOT_INTEGRATION_USER_ID"
    )
    PUBLIC_BACKEND_URL: str | None = os.getenv("PUBLIC_BACKEND_URL")
    # CSAT omnichannel: base URL trang đánh giá FE, VD https://app.example/rate
    # Link gửi khách = {PUBLIC_RATING_BASE_URL}/{token}
    PUBLIC_RATING_BASE_URL: str | None = os.getenv("PUBLIC_RATING_BASE_URL")
    RATING_TOKEN_EXPIRE_HOURS: int = int(os.getenv("RATING_TOKEN_EXPIRE_HOURS", "72"))
    # Entropy cho token link CSAT (bytes). token_urlsafe(12) ≈ 16 ký tự URL; (32) ≈ 43 ký tự.
    RATING_TOKEN_BYTES: int = int(os.getenv("RATING_TOKEN_BYTES", "12"))
    # Khoảng cách tối thiểu giữa 2 lần gửi CSAT cùng conversation (giờ).
    # VD 1: resolve lại trong < 1h → không gửi; >= 1h → gửi survey mới.
    # 0 = không cooldown (mỗi lần resolve đều được gửi nếu đủ điều kiện khác).
    RATING_RESEND_COOLDOWN_HOURS: float = float(
        os.getenv("RATING_RESEND_COOLDOWN_HOURS", "1")
    )
    # Chống double-fire webhook + toggle_status cùng một lần resolve (giây).
    # Luôn áp dụng kể cả khi RATING_RESEND_COOLDOWN_HOURS=0. 0 = tắt dedupe.
    RATING_RESOLVE_DEDUPE_SECONDS: int = int(
        os.getenv("RATING_RESOLVE_DEDUPE_SECONDS", "60")
    )
    # Knowledge Graph (KG) Chatbot settings
    KG_CORE_URL: str = os.getenv("KG_CORE_URL", "https://techstorehust.site/api/v1/chat/completions/stream")
    KG_CORE_API_KEY: str | None = os.getenv("KG_CORE_API_KEY")
    # Live-chat overlay: TTL Redis chờ mở widget sau POST select (giây). Mặc định 1h.
    LIVE_CHAT_PERSONA_SELECT_TTL_SECONDS: int = int(
        os.getenv("LIVE_CHAT_PERSONA_SELECT_TTL_SECONDS", "3600")
    )
    # Prefix setUser identifier (tránh đụng email/user thật).
    LIVE_CHAT_CLIENT_SESSION_PREFIX: str = os.getenv(
        "LIVE_CHAT_CLIENT_SESSION_PREFIX", "oh_"
    )


settings = Settings()