from fastapi import APIRouter
from app.email_service.email import send_email

router = APIRouter(
    prefix="",
    tags=["Test"]
)

@router.post("/test-email")
async def test_email():
    await send_email(
        from_name="nguyenxuanmanh2992003@gmail.com",
        to=["20210794@eaut.edu.vn", "another@example.com"],
        subject="Chào mừng!",
        template_name="welcome_email.html",
        body_params={"username": "Nguyen Van A"},
        cc=["20210794@eaut.edu.vn"],
        bcc=[]
    )