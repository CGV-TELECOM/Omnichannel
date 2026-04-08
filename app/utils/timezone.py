"""
Timezone Utility Module - Centralized Timezone Configuration
============================================================

This module provides centralized timezone handling for the entire application.
All datetime conversions should use functions from this module to ensure consistency.

Configuration:
    - Timezone is configured in app_config.py via TIMEZONE setting
    - Default: Asia/Ho_Chi_Minh (UTC+7 - Vietnam Time)
    - Can be overridden via TIMEZONE environment variable

Usage:
    from app.utils.timezone import convert_to_app_timezone, get_current_time
    
    # Convert datetime to app timezone
    vietnam_time_str = convert_to_app_timezone(some_datetime)
    
    # Get current time in app timezone
    current_time = get_current_time()
"""

from datetime import datetime
from typing import Optional, Any, Dict, List
from zoneinfo import ZoneInfo
from app.core.config.app_config import settings

# Cache timezone object for performance
_APP_TIMEZONE = ZoneInfo(settings.TIMEZONE)


def get_app_timezone() -> ZoneInfo:
    """
    Get the application's configured timezone.
    
    Returns:
        ZoneInfo: The configured timezone object
    
    Example:
        >>> tz = get_app_timezone()
        >>> print(tz)  # Asia/Ho_Chi_Minh
    """
    return _APP_TIMEZONE


def convert_to_app_timezone(dt: Optional[datetime]) -> Optional[str]:
    """
    Convert datetime to application timezone and return ISO format string.
    
    This is the main function to use for datetime serialization in API responses.
    
    Args:
        dt: Datetime object (usually UTC from database) or None
    
    Returns:
        ISO format string with application timezone, or None if input is None
    
    Examples:
        >>> # UTC datetime from database
        >>> utc_time = datetime(2026, 1, 12, 7, 46, 0, tzinfo=ZoneInfo("UTC"))
        >>> convert_to_app_timezone(utc_time)
        '2026-01-12T14:46:00+07:00'  # Vietnam time (UTC+7)
        
        >>> # Naive datetime (assumed UTC)
        >>> naive_time = datetime(2026, 1, 12, 7, 46, 0)
        >>> convert_to_app_timezone(naive_time)
        '2026-01-12T14:46:00+07:00'  # Converted to Vietnam time
        
        >>> # None value
        >>> convert_to_app_timezone(None)
        None
    """
    if dt is None:
        return None
    
    # If datetime is naive (no timezone), assume it's UTC
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=ZoneInfo("UTC"))
    
    # Convert to application timezone
    app_time = dt.astimezone(_APP_TIMEZONE)
    
    return app_time.isoformat()


def get_current_time() -> datetime:
    """
    Get current time in application timezone.
    
    Returns:
        datetime: Current time with application timezone
    
    Example:
        >>> now = get_current_time()
        >>> print(now)  # 2026-01-12 14:46:00+07:00
    """
    return datetime.now(_APP_TIMEZONE)


def get_current_time_str() -> str:
    """
    Get current time as ISO format string in application timezone.
    
    Returns:
        str: Current time in ISO format
    
    Example:
        >>> now_str = get_current_time_str()
        >>> print(now_str)  # '2026-01-12T14:46:00+07:00'
    """
    return get_current_time().isoformat()


def convert_datetime_fields(obj: Any) -> Any:
    """
    Recursively convert all datetime objects in a data structure to timezone-aware ISO strings.
    
    This function handles:
    - datetime objects -> converted to app timezone ISO string
    - dict -> recursively process all values
    - list/tuple -> recursively process all items
    - other types -> return as-is
    
    Args:
        obj: Any Python object (dict, list, datetime, primitive, etc.)
    
    Returns:
        Same structure with all datetime objects converted to ISO strings
    
    Examples:
        >>> data = {
        ...     "created_at": datetime(2026, 1, 12, 7, 46, 0, tzinfo=ZoneInfo("UTC")),
        ...     "user": {
        ...         "name": "John",
        ...         "registered_at": datetime(2026, 1, 1, 0, 0, 0, tzinfo=ZoneInfo("UTC"))
        ...     },
        ...     "events": [
        ...         {"time": datetime(2026, 1, 10, 10, 0, 0, tzinfo=ZoneInfo("UTC"))}
        ...     ]
        ... }
        >>> result = convert_datetime_fields(data)
        >>> print(result["created_at"])  # '2026-01-12T14:46:00+07:00'
        >>> print(result["user"]["registered_at"])  # '2026-01-01T07:00:00+07:00'
        >>> print(result["events"][0]["time"])  # '2026-01-10T17:00:00+07:00'
    """
    if isinstance(obj, datetime):
        return convert_to_app_timezone(obj)
    elif isinstance(obj, dict):
        return {key: convert_datetime_fields(value) for key, value in obj.items()}
    elif isinstance(obj, list):
        return [convert_datetime_fields(item) for item in obj]
    elif isinstance(obj, tuple):
        return tuple(convert_datetime_fields(item) for item in obj)
    elif isinstance(obj, set):
        # Note: sets can't contain unhashable types like dicts
        return {convert_datetime_fields(item) for item in obj}
    # Handle Pydantic models
    elif hasattr(obj, 'model_dump'):
        return convert_datetime_fields(obj.model_dump())
    elif hasattr(obj, 'dict'):
        return convert_datetime_fields(obj.dict())
    else:
        return obj


def parse_datetime(dt_str: str, input_timezone: Optional[str] = None) -> datetime:
    """
    Parse datetime string and convert to application timezone.
    
    Args:
        dt_str: ISO format datetime string
        input_timezone: Optional timezone of input string (default: UTC)
    
    Returns:
        datetime: Parsed datetime in application timezone
    
    Example:
        >>> dt = parse_datetime("2026-01-12T07:46:00Z")
        >>> print(dt)  # 2026-01-12 14:46:00+07:00 (Vietnam time)
    """
    dt = datetime.fromisoformat(dt_str.replace('Z', '+00:00'))
    
    if dt.tzinfo is None:
        tz = ZoneInfo(input_timezone) if input_timezone else ZoneInfo("UTC")
        dt = dt.replace(tzinfo=tz)
    
    return dt.astimezone(_APP_TIMEZONE)


# Export commonly used functions
__all__ = [
    'get_app_timezone',
    'convert_to_app_timezone',
    'get_current_time',
    'get_current_time_str',
    'convert_datetime_fields',
    'parse_datetime',
]
