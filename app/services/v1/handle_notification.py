"""
Notification Service
Handles creation, storage, and real-time delivery of notifications
"""
from uuid import UUID
from typing import Optional, Dict, List
from datetime import datetime, timezone
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, or_, update
from app.core.socket.manager import socket_manager
from app.schemas.responses.api_response_rule import api_response, ResponseStatus, ResponseStatusCode
from app.db.models import Notification, NotificationType as NotificationTypeEnum, User
import logging
import json

logger = logging.getLogger(__name__)

class NotificationType:
    """Notification types"""
    INFO = "info"
    SUCCESS = "success"
    WARNING = "warning"
    ERROR = "error"
    SYSTEM = "system"
    USER_ACTION = "user_action"
    TICKET_UPDATE = "ticket_update"
    MESSAGE = "message"

class NotificationService:
    """
    Service for managing notifications
    """
    
    @staticmethod
    async def send_notification_to_user(
        user_id: UUID,
        title: str,
        message: str,
        notification_type: str = NotificationType.INFO,
        data: Optional[Dict] = None,
        db: Optional[AsyncSession] = None,
        sender_id: Optional[UUID] = None
    ):
        """
        Send real-time notification to specific user with persistent storage
        """
        try:
            # Get user's tenant_id
            tenant_id = None
            if db:
                user_result = await db.execute(select(User).where(User.id == user_id))
                user = user_result.scalar_one_or_none()
                if user:
                    tenant_id = user.tenant_id
            
            # Save to database first for persistence
            notification_record = None
            if db:
                notification_record = Notification(
                    title=title,
                    message=message,
                    type=getattr(NotificationTypeEnum, notification_type.upper(), NotificationTypeEnum.INFO),
                    user_id=user_id,
                    tenant_id=tenant_id,
                    sender_id=sender_id,
                    data=json.dumps(data) if data else None,
                    delivered=0,  # Not delivered yet
                    is_read=0
                )
                db.add(notification_record)
                await db.commit()
                await db.refresh(notification_record)
            
            notification_data = {
                "id": str(notification_record.id) if notification_record else None,
                "title": title,
                "message": message,
                "type": notification_type,
                "timestamp": datetime.now(timezone.utc),
                "data": data or {},
                "read": False
            }
            
            # Try to send via WebSocket
            result = await socket_manager.send_to_user(
                user_id=user_id,
                event='notification',
                data=notification_data
            )
            
            # Mark as delivered if successfully sent
            if result and notification_record and db:
                notification_record.delivered = 1
                notification_record.delivered_at = datetime.now(timezone.utc)
                await db.commit()
            # This allows users to see notifications even if they were offline
            
            logger.info(f"Sent notification to user {user_id}: {title}")
            return True
            
        except Exception as e:
            logger.error(f"Error sending notification to user {user_id}: {str(e)}")
            return False
    
    @staticmethod
    async def send_notification_to_tenant(
        tenant_id: UUID,
        title: str,
        message: str,
        notification_type: str = NotificationType.INFO,
        data: Optional[Dict] = None,
        exclude_user_id: Optional[UUID] = None
    ):
        """
        Send real-time notification to all users in a tenant
        """
        try:
            notification_data = {
                "title": title,
                "message": message,
                "type": notification_type,
                "timestamp": datetime.now(timezone.utc),
                "data": data or {},
                "read": False
            }
            
            # Send via WebSocket
            await socket_manager.send_to_tenant(
                tenant_id=tenant_id,
                event='notification',
                data=notification_data
            )
            
            logger.info(f"Sent notification to tenant {tenant_id}: {title}")
            return True
            
        except Exception as e:
            logger.error(f"Error sending notification to tenant {tenant_id}: {str(e)}")
            return False
    
    @staticmethod
    async def broadcast_notification(
        title: str,
        message: str,
        notification_type: str = NotificationType.SYSTEM,
        data: Optional[Dict] = None
    ):
        """
        Broadcast notification to all connected users
        """
        try:
            notification_data = {
                "title": title,
                "message": message,
                "type": notification_type,
                "timestamp": datetime.now(timezone.utc),
                "data": data or {},
                "read": False
            }
            
            # Send via WebSocket
            await socket_manager.broadcast(
                event='notification',
                data=notification_data
            )
            
            logger.info(f"Broadcasted notification: {title}")
            return True
            
        except Exception as e:
            logger.error(f"Error broadcasting notification: {str(e)}")
            return False
    
    @staticmethod
    async def notify_user_kicked(user_id: UUID, reason: str = "Your account has been deactivated"):
        """
        Notify user that they've been kicked/deactivated
        """
        try:
            await NotificationService.send_notification_to_user(
                user_id=user_id,
                title="Account Deactivated",
                message=reason,
                notification_type=NotificationType.ERROR,
                data={"action": "kicked"}
            )
            
            # Disconnect user after short delay
            await socket_manager.disconnect_user(user_id, reason)
            
            logger.info(f"Notified and disconnected kicked user {user_id}")
            return True
            
        except Exception as e:
            logger.error(f"Error notifying kicked user {user_id}: {str(e)}")
            return False
    
    @staticmethod
    async def notify_password_changed(user_id: UUID):
        """
        Notify user that their password was changed
        """
        try:
            await NotificationService.send_notification_to_user(
                user_id=user_id,
                title="Password Changed",
                message="Your password has been changed. Please login again with your new password.",
                notification_type=NotificationType.WARNING,
                data={"action": "password_changed"}
            )
            
            # Disconnect user to force re-login
            await socket_manager.disconnect_user(
                user_id, 
                "Password changed. Please login again."
            )
            
            logger.info(f"Notified user {user_id} about password change")
            return True
            
        except Exception as e:
            logger.error(f"Error notifying password change for user {user_id}: {str(e)}")
            return False
    
    @staticmethod
    async def notify_role_changed(user_id: UUID, new_role: str):
        """
        Notify user that their role was changed
        """
        try:
            await NotificationService.send_notification_to_user(
                user_id=user_id,
                title="Role Updated",
                message=f"Your role has been changed to: {new_role}",
                notification_type=NotificationType.INFO,
                data={
                    "action": "role_changed",
                    "new_role": new_role
                }
            )
            
            logger.info(f"Notified user {user_id} about role change to {new_role}")
            return True
            
        except Exception as e:
            logger.error(f"Error notifying role change for user {user_id}: {str(e)}")
            return False
    
    @staticmethod
    async def notify_ticket_assigned(user_id: UUID, ticket_id: UUID, ticket_title: str):
        """
        Notify user that a ticket was assigned to them
        """
        try:
            await NotificationService.send_notification_to_user(
                user_id=user_id,
                title="New Ticket Assigned",
                message=f"You have been assigned to ticket: {ticket_title}",
                notification_type=NotificationType.TICKET_UPDATE,
                data={
                    "action": "ticket_assigned",
                    "ticket_id": str(ticket_id),
                    "ticket_title": ticket_title
                }
            )
            
            logger.info(f"Notified user {user_id} about ticket assignment {ticket_id}")
            return True
            
        except Exception as e:
            logger.error(f"Error notifying ticket assignment for user {user_id}: {str(e)}")
            return False
    
    @staticmethod
    async def notify_ticket_status_changed(
        user_id: UUID,
        ticket_id: UUID,
        ticket_title: str,
        old_status: str,
        new_status: str
    ):
        """
        Notify user about ticket status change
        """
        try:
            await NotificationService.send_notification_to_user(
                user_id=user_id,
                title="Ticket Status Updated",
                message=f"Ticket '{ticket_title}' status changed from {old_status} to {new_status}",
                notification_type=NotificationType.TICKET_UPDATE,
                data={
                    "action": "ticket_status_changed",
                    "ticket_id": str(ticket_id),
                    "ticket_title": ticket_title,
                    "old_status": old_status,
                    "new_status": new_status
                }
            )
            
            logger.info(f"Notified user {user_id} about ticket {ticket_id} status change")
            return True
            
        except Exception as e:
            logger.error(f"Error notifying ticket status change for user {user_id}: {str(e)}")
            return False
    
    @staticmethod
    async def get_online_users_count() -> int:
        """Get count of online users"""
        return len(socket_manager.user_sessions)
    
    @staticmethod
    async def get_online_users() -> List[Dict]:
        """Get list of online users"""
        return socket_manager.get_connected_users()
    
    @staticmethod
    async def is_user_online(user_id: UUID) -> bool:
        """Check if user is online"""
        return socket_manager.is_user_online(user_id)
    
    @staticmethod
    async def get_undelivered_notifications(user_id: UUID, db: AsyncSession, limit: int = 50) -> List[Notification]:
        """
        Get all undelivered notifications for a user
        Used when user reconnects to send missed notifications
        """
        try:
            query = select(Notification).where(
                and_(
                    Notification.user_id == user_id,
                    Notification.delivered == 0,
                    or_(
                        Notification.expires_at == None,
                        Notification.expires_at > datetime.now(timezone.utc)
                    )
                )
            ).order_by(Notification.created_at.asc()).limit(limit)
            
            result = await db.execute(query)
            notifications = result.scalars().all()
            
            logger.info(f"Found {len(notifications)} undelivered notifications for user {user_id}")
            return notifications
            
        except Exception as e:
            logger.error(f"Error fetching undelivered notifications: {str(e)}")
            return []
    
    @staticmethod
    async def send_missed_notifications(user_id: UUID, db: AsyncSession):
        """
        Send all missed notifications to user when they reconnect
        """
        try:
            # Get undelivered notifications
            notifications = await NotificationService.get_undelivered_notifications(user_id, db)
            
            if not notifications:
                logger.info(f"No missed notifications for user {user_id}")
                return 0
            
            sent_count = 0
            for notification in notifications:
                try:
                    notification_data = {
                        "id": str(notification.id),
                        "title": notification.title,
                        "message": notification.message,
                        "type": notification.type.value if hasattr(notification.type, 'value') else notification.type,
                        "timestamp": notification.created_at,
                        "data": json.loads(notification.data) if notification.data else {},
                        "read": bool(notification.is_read),
                        "missed": True  # Flag to indicate this is a missed notification
                    }
                    
                    # Send via WebSocket
                    result = await socket_manager.send_to_user(
                        user_id=user_id,
                        event='notification',
                        data=notification_data
                    )
                    
                    # Mark as delivered if successfully sent
                    if result:
                        notification.delivered = 1
                        notification.delivered_at = datetime.now(timezone.utc)
                        sent_count += 1
                    
                except Exception as e:
                    logger.error(f"Error sending missed notification {notification.id}: {str(e)}")
                    continue
            
            # Commit all updates
            await db.commit()
            
            logger.info(f"Sent {sent_count}/{len(notifications)} missed notifications to user {user_id}")
            return sent_count
            
        except Exception as e:
            logger.error(f"Error sending missed notifications: {str(e)}")
            return 0
    
    @staticmethod
    async def mark_notification_as_read(notification_id: UUID, db: AsyncSession):
        """Mark a notification as read"""
        try:
            stmt = (
                update(Notification)
                .where(Notification.id == notification_id)
                .values(is_read=1, read_at=datetime.now(timezone.utc))
            )
            await db.execute(stmt)
            await db.commit()
            return True
        except Exception as e:
            logger.error(f"Error marking notification as read: {str(e)}")
            return False
    
    @staticmethod
    async def mark_all_as_read(user_id: UUID, db: AsyncSession):
        """Mark all notifications as read for a user"""
        try:
            stmt = (
                update(Notification)
                .where(and_(Notification.user_id == user_id, Notification.is_read == 0))
                .values(is_read=1, read_at=datetime.now(timezone.utc))
            )
            await db.execute(stmt)
            await db.commit()
            return True
        except Exception as e:
            logger.error(f"Error marking all notifications as read: {str(e)}")
            return False
    
    @staticmethod
    async def get_notification_history(
        user_id: UUID,
        db: AsyncSession,
        page: int = 1,
        page_size: int = 20,
        unread_only: bool = False
    ):
        """Get notification history for a user with pagination"""
        try:
            conditions = [Notification.user_id == user_id]
            if unread_only:
                conditions.append(Notification.is_read == 0)
            
            query = select(Notification).where(
                and_(*conditions)
            ).order_by(Notification.created_at.desc())
            
            # Pagination
            offset = (page - 1) * page_size
            query = query.limit(page_size).offset(offset)
            
            result = await db.execute(query)
            notifications = result.scalars().all()
            
            # Convert to dict
            notification_list = []
            for n in notifications:
                notification_list.append({
                    "id": str(n.id),
                    "title": n.title,
                    "message": n.message,
                    "type": n.type.value if hasattr(n.type, 'value') else n.type,
                    "is_read": bool(n.is_read),
                    "delivered": bool(n.delivered),
                    "created_at": n.created_at,
                    "read_at": n.read_at,
                    "data": json.loads(n.data) if n.data else {}
                })
            
            return notification_list
            
        except Exception as e:
            logger.error(f"Error fetching notification history: {str(e)}")
            return []

# Global notification service instance
notification_service = NotificationService()
