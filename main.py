from fastapi import FastAPI, Depends
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
import asyncpg
import asyncio
from fastapi.middleware.cors import CORSMiddleware
from uuid import UUID
# from app.middleware.rate_limiter import RateLimitMiddleware
from app.core.security.jwt import verify_token
from app.api.v1.endpoints.auth import router as router_auth
from app.api.v1.endpoints.user import router as router_user
from app.api.v1.endpoints.role_permission import router as router_role_permission
from app.schemas.responses.api_response_rule import api_response, convert_uuid_to_str
from app.schemas.responses.api_response_rule import ResponseStatus, ResponseStatusCode
from app.core.config.database import async_session_maker
from app.seeds.rbac import seed_rbac
from app.api.v1.endpoints.permissions import router as router_permissions
from app.api.v1.endpoints.role import router as router_role
from app.api.v1.endpoints.log import router as router_log
from app.api.v1.endpoints.level import router as router_level
from app.api.v1.endpoints.department import router as router_department
from app.api.v1.endpoints.group import router as router_group
from app.api.v1.endpoints.user_group import router as router_user_group
from app.api.v1.endpoints.test_email import router as router_test_email
from app.api.v1.endpoints.tenant import router as router_tenant
from app.api.v1.endpoints.tag import router as router_tag
from app.api.v1.endpoints.ticket_event import router as router_ticket_event
from app.api.v1.endpoints.ticket_template import router as router_ticket_template
from app.api.v1.endpoints.ticket_context import router as router_ticket_context
from app.api.v1.endpoints.ticket_extension import router as router_ticket_extension
from app.api.v1.endpoints.ticket import router as router_ticket
from app.api.v1.endpoints.ticket_flow import router as router_ticket_flow
from app.api.v1.endpoints.ticket_flow_instance import router as router_ticket_flow_instance
from app.api.v1.endpoints.ticket_flow_step import router as router_ticket_flow_step
from app.api.v1.endpoints.customer import router as router_customer
from app.api.v1.endpoints.chatwoot import router as router_chatwoot
from app.api.v1.endpoints.chatwoot.webhook import router as router_chatwoot_webhook
# WebSocket & Notifications
from app.core.socket.manager import socket_manager
from app.api.v1.endpoints.notification import router as router_notification

# Custom JSON encoder for UUID
def custom_jsonable_encoder(obj):
    """Custom encoder that handles UUID objects"""
    if isinstance(obj, UUID):
        return str(obj)
    return jsonable_encoder(obj)

app = FastAPI()
# app.add_middleware(RateLimitMiddleware)

app.include_router(router_auth, prefix="/api/v1", tags=["Auth"])
app.include_router(router_test_email, prefix="/api/v1", tags=["Test"])
app.include_router(router_chatwoot_webhook, prefix="/api/v1")
protected_routers = [
    router_user,
    router_role_permission,
    router_role,
    router_log,
    router_level,
    router_permissions,
    router_department,
    router_group,
    router_user_group,
    router_tenant,
    router_tag,
    router_ticket,
    router_ticket_event,
    router_ticket_template,
    router_ticket_context,
    router_ticket_extension,
    router_ticket_flow,
    router_ticket_flow_instance,
    router_ticket_flow_step,
    router_customer,
    router_notification,
    router_chatwoot,
]

origins = [
    "http://localhost:5173",
    "http://52.221.226.79:5173",
    "http://localhost:3000",
    "https://devomnichannelcgv.telesip.vn",
    "*"
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "Accept"],
)


for router in protected_routers:
    app.include_router(router, prefix="/api/v1", dependencies=[Depends(verify_token)])


# Custom response handler to ensure UUID serialization
@app.middleware("http")
async def uuid_serialization_middleware(request, call_next):
    response = await call_next(request)
    # If response body contains UUID, it will be handled by api_response function
    return response


@app.get("/")
def read_root():
    return api_response(status=ResponseStatus.SUCCESS, message="Welcome to the API", data=None, status_code=ResponseStatusCode.OK)

@app.get("/ws/status")
def websocket_status():
    """Get WebSocket server status"""
    return api_response(
        status=ResponseStatus.SUCCESS,
        message="WebSocket server is running",
        data={
            "connected_users": len(socket_manager.user_sessions),
            "total_connections": len(socket_manager.connections),
            "endpoint": "/socket.io"
        },
        status_code=ResponseStatusCode.OK
    )

# Mount Socket.IO app
app.mount("/socket.io", socket_manager.socket_app)

#@app.on_event("startup")
#async def on_startup():
#    async with async_session_maker() as session:
#        await seed_rbac(session)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
