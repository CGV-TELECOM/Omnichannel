# utils/response_helper.py
from typing import Any, Optional
from enum import Enum
from uuid import UUID
import json
from fastapi.responses import JSONResponse
from datetime import datetime

class ResponseStatus(str, Enum):
    """Enum cho các trạng thái response chuẩn"""
    SUCCESS = "success"
    ERROR = "error"
    WARNING = "warning"
    INFO = "info"
    
class ResponseStatusCode(int, Enum):
    """Enum cho các mã trạng thái HTTP"""
    OK = 200
    CREATED = 201
    NO_CONTENT = 204
    BAD_REQUEST = 400
    UNAUTHORIZED = 401
    FORBIDDEN = 403
    NOT_FOUND = 404
    METHOD_NOT_ALLOWED = 405
    CONFLICT = 409
    TOMANY_REQUESTS = 429
    INTERNAL_SERVER_ERROR = 500
    SERVICE_UNAVAILABLE = 503
    

def convert_uuid_to_str(obj: Any) -> Any:
    """
    Recursively convert UUID objects to strings for JSON serialization.
    Handles UUID objects, dicts, lists, tuples, sets, and Pydantic models.
    
    NOTE: This function is kept for backward compatibility.
    New code should use convert_for_json() which handles both UUID and datetime.
    """
    if isinstance(obj, UUID):
        return str(obj)
    elif isinstance(obj, dict):
        return {key: convert_uuid_to_str(value) for key, value in obj.items()}
    elif isinstance(obj, list):
        return [convert_uuid_to_str(item) for item in obj]
    elif isinstance(obj, tuple):
        return tuple(convert_uuid_to_str(item) for item in obj)
    elif isinstance(obj, set):
        return {convert_uuid_to_str(item) for item in obj}
    # Handle Pydantic models
    elif hasattr(obj, 'model_dump'):
        return convert_uuid_to_str(obj.model_dump())
    elif hasattr(obj, 'dict'):
        return convert_uuid_to_str(obj.dict())
    else:
        return obj


def convert_for_json(obj: Any) -> Any:
    """
    Recursively convert UUID and datetime objects for JSON serialization.
    
    Handles:
    - UUID objects -> converted to strings
    - datetime objects -> converted to app timezone ISO strings
    - dict -> recursively process all values
    - list/tuple/set -> recursively process all items
    - Pydantic models -> extract dict and process
    - SQLAlchemy models -> convert to dict (only include column attributes, exclude relationships)
    
    This is the main conversion function used by api_response().
    """
    # Import here to avoid circular imports
    from app.utils.timezone import convert_to_app_timezone
    
    if isinstance(obj, UUID):
        return str(obj)
    elif isinstance(obj, datetime):
        return convert_to_app_timezone(obj)
    elif isinstance(obj, dict):
        return {key: convert_for_json(value) for key, value in obj.items()}
    elif isinstance(obj, list):
        return [convert_for_json(item) for item in obj]
    elif isinstance(obj, tuple):
        return tuple(convert_for_json(item) for item in obj)
    elif isinstance(obj, set):
        return {convert_for_json(item) for item in obj}
    # Handle SQLAlchemy models (check for __table__ attribute which indicates a SQLAlchemy model)
    elif hasattr(obj, '__table__'):
        # Convert SQLAlchemy model to dict, only including column attributes
        # Exclude relationships to avoid circular references and non-serializable objects
        result = {}
        for column in obj.__table__.columns:
            value = getattr(obj, column.name, None)
            result[column.name] = convert_for_json(value)
        return result
    # Handle Pydantic models
    elif hasattr(obj, 'model_dump'):
        return convert_for_json(obj.model_dump())
    elif hasattr(obj, 'dict'):
        return convert_for_json(obj.dict())
    else:
        return obj
    

def api_response(
    status: ResponseStatus,
    status_code: ResponseStatusCode,
    message: str,
    data: Any = None
) -> dict:
    """
    Create a standardized API response with automatic UUID and datetime conversion.
    
    Features:
    - Converts UUID objects to strings
    - Converts datetime objects to application timezone (configured in settings)
    - Handles nested structures (dicts, lists, Pydantic models)
    - Automatic error handling and fallback
    
    Args:
        status: Response status (SUCCESS, ERROR, etc.)
        status_code: HTTP status code (200, 201, 404, etc.)
        message: Human-readable message
        data: Response data (any type)
    
    Returns:
        dict: Standardized response dictionary ready for JSON serialization
    """
    try:
        # Convert UUID and datetime objects for JSON serialization
        converted_data = convert_for_json(data) if data is not None else None
        
        response_dict = {
            "status": status.value if isinstance(status, ResponseStatus) else status,
            "status_code": status_code.value if isinstance(status_code, ResponseStatusCode) else status_code,
        "message": message,
            "data": converted_data
        }
        
        # Double check: try to serialize to ensure no UUID or datetime remains
        # This will raise an exception if there are still non-serializable objects
        try:
            json.dumps(response_dict)
        except (TypeError, ValueError) as serialization_error:
            # If serialization fails, try to convert again more aggressively
            response_dict["data"] = convert_for_json(response_dict["data"])
            json.dumps(response_dict)  # Try again
        
        return response_dict
    except Exception as e:
        # If there's still a serialization error, return a safe error response
        # Make sure we don't use json module here to avoid circular issues
        return {
            "status": "error",
            "status_code": 500,
            "message": "Lỗi khi serialize response",
            "data": str(e) if e else "Unknown error"
        }

