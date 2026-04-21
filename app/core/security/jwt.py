from datetime import timedelta, datetime, timezone
from jose import jwt, JWTError
from fastapi import Request, HTTPException, status
from app.core.config.app_config import settings
from app.schemas.responses.api_response_rule import api_response, ResponseStatus, ResponseStatusCode
from uuid import UUID

# Lấy các cấu hình từ settings
ALGORITHM = settings.ALGORITHM
SECRET_KEY = settings.SECRET_KEY
ACCESS_TOKEN_EXPIRE_MINUTES = int(settings.ACCESS_TOKEN_EXPIRE_MINUTES)
REFRESH_TOKEN_EXPIRE_DAYS = int(settings.REFRESH_TOKEN_EXPIRE_DAYS)

def create_access_token(data: dict):
    """
    Tạo access token JWT với thời gian hết hạn (UTC timezone cho JWT standard)
    
    Args:
        data (dict): Dữ liệu cần encode vào token, thường chứa user_id trong key 'sub'
        
    Returns:
        str: JWT token đã được encode
    
    Note:
        JWT exp claim sử dụng UTC timestamp theo chuẩn (RFC 7519)
    """
    to_encode = data.copy()
    current_time = datetime.now(timezone.utc)
    expire = current_time + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({
        "exp": expire,
        "token_type": "access"
    })
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt

def create_refresh_token(data: dict):
    """
    Tạo refresh token JWT với thời gian hết hạn dài hơn access token
    
    Args:
        data (dict): Dữ liệu cần encode vào token, thường chứa user_id trong key 'sub'
        
    Returns:
        str: JWT token đã được encode
    
    Note:
        JWT exp claim sử dụng UTC timestamp theo chuẩn (RFC 7519)
    """
    to_encode = data.copy()
    current_time = datetime.now(timezone.utc)
    expire = current_time + timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS)
    to_encode.update({
        "exp": expire,
        "token_type": "refresh"
    })
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt

def verify_token(request: Request):
    """
    Xác thực JWT token từ Authorization header
    Args:
        request (Request): FastAPI request object
    Returns:
        str: user_id từ token nếu xác thực thành công
    Raises:
        HTTPException: Với các trường hợp lỗi khác nhau
    """
    try:
        auth_header = request.headers.get("Authorization")
        if not auth_header:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Missing Authorization header",
                headers={"WWW-Authenticate": "Bearer"},
            )
            # return api_response(
            #     status=ResponseStatus.ERROR,
            #     status_code=ResponseStatusCode.UNAUTHORIZED,
            #     message="Missing Authorization header",
            #     data=None
            # )

        parts = auth_header.split()
        if len(parts) != 2 or parts[0].lower() != "bearer":
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid authentication credentials",
                headers={"WWW-Authenticate": "Bearer"},
            )
        token = parts[1]

        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        
        # Kiểm tra loại token
        token_type = payload.get("token_type")
        if token_type != "access":
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid token type. Access token required",
                headers={"WWW-Authenticate": "Bearer"},
            )

        user_id = payload.get("user_id")
        if user_id is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid token payload",
                headers={"WWW-Authenticate": "Bearer"},
            )
            
        # tenant = payload.get('tenant')
        # if tenant is None:
        #     raise HTTPException(
        #         status_code=status.HTTP_401_UNAUTHORIZED,
        #         detail="Invalid token payload",
        #         headers={"WWW-Authenticate": "Bearer"},
        #     )

        return UUID(user_id) if isinstance(user_id, str) else user_id
    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token has expired",
            headers={"WWW-Authenticate": "Bearer"},
        )
    except JWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )

def verify_refresh_token(request: Request) -> UUID:
    """
    Xác thực refresh token
    
    Args:
        token (str): Refresh token cần xác thực
        
    Returns:
        int: user_id từ token nếu xác thực thành công
        
    Raises:
        HTTPException: Với các trường hợp lỗi khác nhau
    """
    try:
        auth_header = request.headers.get("Authorization")
        if not auth_header:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Missing Authorization header",
                headers={"WWW-Authenticate": "Bearer"},
            )
        
        parts = auth_header.split()
        if len(parts) != 2 or parts[0].lower() != "bearer":
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid authentication credentials",
                headers={"WWW-Authenticate": "Bearer"}
            )
        
        token = parts[1]
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        
        # Kiểm tra loại token
        token_type = payload.get("token_type")
        if token_type != "refresh":
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid token type. Refresh token required",
                headers={"WWW-Authenticate": "Bearer"},
            )

        user_id = payload.get("user_id")
        if user_id is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid token payload",
                headers={"WWW-Authenticate": "Bearer"},
            )

        return UUID(user_id) if isinstance(user_id, str) else user_id
    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Refresh token has expired",
            headers={"WWW-Authenticate": "Bearer"},
        )
    except JWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate refresh token",
            headers={"WWW-Authenticate": "Bearer"},
        )

def decode_access_token(token: str) -> dict | None:
    """
    Giải mã JWT token mà không raise exception
    
    Args:
        token (str): JWT token cần decode
    Returns:
        dict | None: Payload của token nếu decode thành công, None nếu thất bại
    """
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        if payload.get("token_type") != "access":
            return None
        return payload
    except (jwt.ExpiredSignatureError, JWTError):
        return None
    
def get_user_id_from_token(token: str) -> UUID:
    try:
        token = token.split(" ")[1] if " " in token else token
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        if payload.get("token_type") != "access":
            raise HTTPException(status_code=401, detail="Invalid token type")
        user_id = payload.get("user_id")
        if user_id is None:
            raise HTTPException(status_code=401, detail="User ID not found in token")
        return UUID(user_id) if isinstance(user_id, str) else user_id
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid token")
    except (TypeError, ValueError) as e:
        raise HTTPException(status_code=401, detail="Invalid user_id in token")
