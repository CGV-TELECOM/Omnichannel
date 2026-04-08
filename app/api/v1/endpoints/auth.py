from fastapi import APIRouter, Depends, Request
from app.schemas.requests.auth import LoginRequest, RegisterRequest    
from app.core.config.database import get_db
from sqlalchemy.ext.asyncio import AsyncSession
from app.services.v1 import handle_auth
from app.core.config.logging import log_user_action

router = APIRouter(
    prefix="/auth",
    tags=["Auth"]
)
@router.get("/access_token")
async def get_access_token(request: Request, db: AsyncSession = Depends(get_db)):
    return await handle_auth.get_access_token(request, db)

@router.post("/login")
async def login(form_data: LoginRequest,  request : Request, db: AsyncSession = Depends(get_db)):
    return await handle_auth.login(form_data, request, db)

@router.post("/logout")
@log_user_action("log_out")
async def logout(request: Request, db: AsyncSession = Depends(get_db)):
    return await handle_auth.logout(request, db)
    