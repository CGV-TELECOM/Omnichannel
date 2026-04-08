"""
Notification API Endpoints
Trigger notifications and manage real-time communications
"""
from fastapi import APIRouter, Depends, HTTPException
from uuid import UUID
from typing import Optional, List
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from app.services.v1.handle_notification import notification_service, NotificationType
from app.schemas.responses.api_response_rule import api_response, ResponseStatus, ResponseStatusCode
from app.services.v1.handle_user import get_current_user
from app.db.models import User
from app.core.socket.manager import socket_manager
from app.core.config.database import get_db

router = APIRouter(prefix="/notifications", tags=["Notifications"])

class SendNotificationRequest(BaseModel):
    """Request schema for sending notification"""
    user_id: Optional[UUID] = None
    tenant_id: Optional[UUID] = None
    title: str
    message: str
    type: str = NotificationType.INFO
    data: Optional[dict] = None

class BroadcastNotificationRequest(BaseModel):
    """Request schema for broadcasting notification"""
    title: str
    message: str
    type: str = NotificationType.SYSTEM
    data: Optional[dict] = None

@router.post("/send")
async def send_notification(
    request: SendNotificationRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Send notification to specific user or tenant with persistent storage
    Requires admin or notification permission
    """
    try:
        # Validate notification type
        valid_types = [
            NotificationType.INFO,
            NotificationType.SUCCESS,
            NotificationType.WARNING,
            NotificationType.ERROR,
            NotificationType.SYSTEM,
            NotificationType.USER_ACTION,
            NotificationType.TICKET_UPDATE,
            NotificationType.MESSAGE
        ]
        
        if request.type not in valid_types:
            return api_response(
                status=ResponseStatus.ERROR,
                status_code=ResponseStatusCode.BAD_REQUEST,
                message=f"Invalid notification type. Valid types: {', '.join(valid_types)}"
            )
        
        # Send to specific user
        if request.user_id:
            success = await notification_service.send_notification_to_user(
                user_id=request.user_id,
                title=request.title,
                message=request.message,
                notification_type=request.type,
                data=request.data,
                db=db,
                sender_id=current_user.id
            )
            
            if success:
                return api_response(
                    status=ResponseStatus.SUCCESS,
                    status_code=ResponseStatusCode.OK,
                    message="Notification sent successfully (will be delivered when user connects)",
                    data={
                        "recipient": "user",
                        "user_id": str(request.user_id)
                    }
                )
            else:
                return api_response(
                    status=ResponseStatus.ERROR,
                    status_code=ResponseStatusCode.INTERNAL_SERVER_ERROR,
                    message="Failed to send notification"
                )
        
        # Send to tenant
        elif request.tenant_id:
            count = await notification_service.send_notification_to_tenant(
                tenant_id=request.tenant_id,
                title=request.title,
                message=request.message,
                notification_type=request.type,
                data=request.data
            )
            
            return api_response(
                status=ResponseStatus.SUCCESS,
                status_code=ResponseStatusCode.OK,
                message=f"Notification sent to {count} user(s) in tenant",
                data={
                    "recipient": "tenant",
                    "tenant_id": str(request.tenant_id),
                    "users_notified": count
                }
            )
        
        else:
            return api_response(
                status=ResponseStatus.ERROR,
                status_code=ResponseStatusCode.BAD_REQUEST,
                message="Either user_id or tenant_id must be provided"
            )
    
    except Exception as e:
        return api_response(
            status=ResponseStatus.ERROR,
            status_code=ResponseStatusCode.INTERNAL_SERVER_ERROR,
            message=f"Error sending notification: {str(e)}"
        )

@router.post("/broadcast")
async def broadcast_notification(
    request: BroadcastNotificationRequest,
    current_user: User = Depends(get_current_user)
):
    """
    Broadcast notification to all connected users
    Requires admin permission
    """
    try:
        count = await notification_service.broadcast_notification(
            title=request.title,
            message=request.message,
            notification_type=request.type,
            data=request.data
        )
        
        return api_response(
            status=ResponseStatus.SUCCESS,
            status_code=ResponseStatusCode.OK,
            message=f"Notification broadcasted to {count} user(s)",
            data={
                "recipient": "broadcast",
                "users_notified": count
            }
        )
    
    except Exception as e:
        return api_response(
            status=ResponseStatus.ERROR,
            status_code=ResponseStatusCode.INTERNAL_SERVER_ERROR,
            message=f"Error broadcasting notification: {str(e)}"
        )

@router.get("/online-users")
async def get_online_users(current_user: User = Depends(get_current_user)):
    """
    Get list of online users
    Requires admin permission
    """
    try:
        online_users = await notification_service.get_online_users()
        count = await notification_service.get_online_users_count()
        
        return api_response(
            status=ResponseStatus.SUCCESS,
            status_code=ResponseStatusCode.OK,
            message="Online users retrieved successfully",
            data={
                "count": count,
                "users": online_users
            }
        )
    
    except Exception as e:
        return api_response(
            status=ResponseStatus.ERROR,
            status_code=ResponseStatusCode.INTERNAL_SERVER_ERROR,
            message=f"Error retrieving online users: {str(e)}"
        )

@router.get("/user/{user_id}/online")
async def check_user_online(
    user_id: UUID,
    current_user: User = Depends(get_current_user)
):
    """
    Check if a specific user is online
    """
    try:
        is_online = await notification_service.is_user_online(user_id)
        
        return api_response(
            status=ResponseStatus.SUCCESS,
            status_code=ResponseStatusCode.OK,
            message="User status retrieved",
            data={
                "user_id": str(user_id),
                "is_online": is_online
            }
        )
    
    except Exception as e:
        return api_response(
            status=ResponseStatus.ERROR,
            status_code=ResponseStatusCode.INTERNAL_SERVER_ERROR,
            message=f"Error checking user status: {str(e)}"
        )

@router.get("/ws/status")
async def websocket_status():
    """
    Get WebSocket server status and statistics
    Public endpoint for health check
    """
    try:
        stats = {
            "status": "running",
            "total_connections": len(socket_manager.connections),
            "unique_users_online": len(socket_manager.user_sessions),
            "tenants_with_users": len(socket_manager.tenant_sessions),
            "connections_by_user": {
                str(user_id): len(sids)
                for user_id, sids in socket_manager.user_sessions.items()
            }
        }
        
        return api_response(
            status=ResponseStatus.SUCCESS,
            status_code=ResponseStatusCode.OK,
            message="WebSocket server is running",
            data=stats
        )
        
    except Exception as e:
        return api_response(
            status=ResponseStatus.ERROR,
            status_code=ResponseStatusCode.INTERNAL_SERVER_ERROR,
            message=f"Error retrieving statistics: {str(e)}"
        )

@router.get("/history")
async def get_notification_history(
    page: int = 1,
    page_size: int = 20,
    unread_only: bool = False,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Get notification history for current user
    """
    try:
        from app.services.v1.handle_notification import NotificationService
        
        notifications = await NotificationService.get_notification_history(
            user_id=current_user.id,
            db=db,
            page=page,
            page_size=page_size,
            unread_only=unread_only
        )
        
        return api_response(
            status=ResponseStatus.SUCCESS,
            status_code=ResponseStatusCode.OK,
            message="Notification history retrieved",
            data={
                "notifications": notifications,
                "page": page,
                "page_size": page_size,
                "count": len(notifications)
            }
        )
        
    except Exception as e:
        return api_response(
            status=ResponseStatus.ERROR,
            status_code=ResponseStatusCode.INTERNAL_SERVER_ERROR,
            message=str(e)
        )

@router.post("/{notification_id}/read")
async def mark_notification_as_read(
    notification_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Mark a notification as read
    """
    try:
        from app.services.v1.handle_notification import NotificationService
        
        success = await NotificationService.mark_notification_as_read(notification_id, db)
        
        if success:
            return api_response(
                status=ResponseStatus.SUCCESS,
                status_code=ResponseStatusCode.OK,
                message="Notification marked as read"
            )
        else:
            return api_response(
                status=ResponseStatus.ERROR,
                status_code=ResponseStatusCode.INTERNAL_SERVER_ERROR,
                message="Failed to mark notification as read"
            )
        
    except Exception as e:
        return api_response(
            status=ResponseStatus.ERROR,
            status_code=ResponseStatusCode.INTERNAL_SERVER_ERROR,
            message=str(e)
        )

@router.post("/read-all")
async def mark_all_notifications_as_read(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Mark all notifications as read for current user
    """
    try:
        from app.services.v1.handle_notification import NotificationService
        
        success = await NotificationService.mark_all_as_read(current_user.id, db)
        
        if success:
            return api_response(
                status=ResponseStatus.SUCCESS,
                status_code=ResponseStatusCode.OK,
                message="All notifications marked as read"
            )
        else:
            return api_response(
                status=ResponseStatus.ERROR,
                status_code=ResponseStatusCode.INTERNAL_SERVER_ERROR,
                message="Failed to mark notifications as read"
            )
        
    except Exception as e:
        return api_response(
            status=ResponseStatus.ERROR,
            status_code=ResponseStatusCode.INTERNAL_SERVER_ERROR,
            message=str(e)
        )
