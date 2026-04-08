from pydantic import EmailStr
from typing import Optional, List, Dict
from email.message import EmailMessage
from jinja2 import Environment, FileSystemLoader
# Cấu hình Jinja2
from pathlib import Path
import aiosmtplib
from app.core.config.app_config import settings

# Cấu hình SMTP
SMTP_CONFIG = {
    "host": settings.SMTP_HOST,
    "port": settings.SMTP_PORT,
    "username": settings.SMTP_USERNAME,
    "password": settings.SMTP_PASSWORD,  
}


BASE_DIR = Path(__file__).resolve().parent.parent  
TEMPLATES_DIR = BASE_DIR / "email_service/templates"    
env = Environment(
    loader=FileSystemLoader(str(TEMPLATES_DIR))
)

async def send_email(
    *,
    from_name: str,
    to: List[EmailStr],
    subject: str,
    template_name: str,
    body_params: Dict[str, str],
    cc: Optional[List[EmailStr]] = None,
    bcc: Optional[List[EmailStr]] = None,
):
    cc = cc or []
    bcc = bcc or []

    # Load template và render
    template = env.get_template(template_name)
    html_body = template.render(subject=subject, **body_params)

    # Tạo EmailMessage
    message = EmailMessage()
    message["From"] = f"{from_name} <{SMTP_CONFIG['username']}>"
    message["To"] = ", ".join(to)
    message["Subject"] = subject
    if cc:
        message["Cc"] = ", ".join(cc)
    if bcc:
        message["Bcc"] = ", ".join(bcc)

    message.set_content("This is a fallback plain text message.")
    message.add_alternative(html_body, subtype="html")

    # Gửi email qua SMTP
    await aiosmtplib.send(
        message,
        hostname=SMTP_CONFIG["host"],
        port=SMTP_CONFIG["port"],
        username=SMTP_CONFIG["username"],
        password=SMTP_CONFIG["password"],
        start_tls=True,
    )
