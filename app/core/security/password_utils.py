from passlib.context import CryptContext

# Tạo context mã hóa
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto", bcrypt__rounds=14)

# Mã hóa mật khẩu
def hash_password(password: str) -> str:
    if not isinstance(password, str) or not password:
        raise ValueError("Password is required")
    try:
        return pwd_context.hash(password)
    except Exception as e:
        raise RuntimeError("Cannot hash password")

# Kiểm tra mật khẩu
def verify_password(plain_password: str, hashed_password: str) -> bool:
    if not isinstance(plain_password, str) or not plain_password:
        raise ValueError("Password is required")
    if not isinstance(hashed_password, str) or not hashed_password:
        raise ValueError("Hashed password is required")
    try:
        return pwd_context.verify(plain_password, hashed_password)
    except Exception as e:
        raise RuntimeError("Cannot verify password")