from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config.database import get_db
from app.core.dependencies.dependencies import get_current_user_dependency
from app.db.models import User
from app.email_service.email import send_email
from app.schemas.responses.api_response_rule import (
    api_response,
    ResponseStatus,
    ResponseStatusCode,
)
from app.utils.helpers import isCheckMaxLevel

router = APIRouter(
    prefix="",
    tags=["Test"],
)


@router.post("/test-email")
async def test_email(
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user_dependency),
):
    """
    Endpoint test gửi email — chỉ Super Admin.
    Không còn public.
    """
    if not await isCheckMaxLevel(current_user, db):
        return api_response(
            status=ResponseStatus.ERROR,
            status_code=ResponseStatusCode.FORBIDDEN,
            message="Chỉ Super Admin được phép dùng endpoint test-email",
            data=None,
        )

    try:
        await send_email(
            from_name="nguyenxuanmanh2992003@gmail.com",
            to=["20210794@eaut.edu.vn", "another@example.com"],
            subject="Chào mừng!",
            template_name="welcome_email.html",
            body_params={"username": "Nguyen Van A"},
            cc=["20210794@eaut.edu.vn"],
            bcc=[],
        )
        return api_response(
            status=ResponseStatus.SUCCESS,
            status_code=ResponseStatusCode.OK,
            message="Đã gửi email test",
            data={"requested_by": current_user.username},
        )
    except Exception as e:
        return api_response(
            status=ResponseStatus.ERROR,
            status_code=ResponseStatusCode.INTERNAL_SERVER_ERROR,
            message=f"Gửi email thất bại: {e}",
            data=None,
        )
