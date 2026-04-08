"""
WebSocket Manager for Real-time Notifications
Handles Socket.IO connections, authentication, and message broadcasting
"""
import socketio
from typing import Dict, List, Optional, Set
from uuid import UUID
import logging
from datetime import datetime, timezone
from app.core.config.app_config import settings

logger = logging.getLogger(__name__)

class SocketManager:
    """
    Centralized Socket.IO manager for real-time communications
    Features:
    - Connection management with authentication
    - Room-based broadcasting (per user, per tenant, global)
    - Automatic reconnection handling
    - Connection state tracking
    - Multi-tenant isolation
    """
    
    def __init__(self):
        # Initialize Socket.IO server with ASGI app
        self.sio = socketio.AsyncServer(
            async_mode='asgi',
            cors_allowed_origins='*',  # Configure based on your needs
            logger=True,
            engineio_logger=True,
            ping_timeout=60,  # Timeout for ping/pong
            ping_interval=25,  # Interval for sending ping
            max_http_buffer_size=1e8,  # 100MB max message size
        )
        
        # Socket.IO ASGI app
        self.socket_app = socketio.ASGIApp(
            self.sio,
            socketio_path='/socket.io'
        )
        
        # Connection tracking
        # Format: {sid: {"user_id": UUID, "tenant_id": UUID, "username": str, "connected_at": datetime}}
        self.connections: Dict[str, Dict] = {}
        
        # User to SID mapping for quick lookup
        # Format: {user_id: Set[sid]}
        self.user_sessions: Dict[UUID, Set[str]] = {}
        
        # Tenant to SIDs mapping
        # Format: {tenant_id: Set[sid]}
        self.tenant_sessions: Dict[UUID, Set[str]] = {}
        
        self._register_handlers()
        
    def _register_handlers(self):
        """Register all Socket.IO event handlers"""
        
        @self.sio.event
        async def connect(sid, environ, auth):
            """
            Handle new socket connection
            Client must send auth token in handshake
            """
            logger.info(f"New connection attempt: {sid}")
            
            # For now, accept connection and wait for authentication
            # Authentication will be done via 'authenticate' event
            await self.sio.emit('connection_established', {
                'sid': sid,
                'message': 'Connected. Please authenticate.',
                'timestamp': datetime.now(timezone.utc).isoformat()
            }, room=sid)
            
            return True
        
        @self.sio.event
        async def disconnect(sid):
            """Handle socket disconnection"""
            logger.info(f"Disconnection: {sid}")
            
            # Get connection info before removing
            conn_info = self.connections.get(sid)
            
            if conn_info:
                user_id = conn_info.get('user_id')
                tenant_id = conn_info.get('tenant_id')
                username = conn_info.get('username', 'Unknown')
                
                # Remove from user sessions
                if user_id and user_id in self.user_sessions:
                    self.user_sessions[user_id].discard(sid)
                    if not self.user_sessions[user_id]:
                        del self.user_sessions[user_id]
                
                # Remove from tenant sessions
                if tenant_id and tenant_id in self.tenant_sessions:
                    self.tenant_sessions[tenant_id].discard(sid)
                    if not self.tenant_sessions[tenant_id]:
                        del self.tenant_sessions[tenant_id]
                
                # Remove from connections
                del self.connections[sid]
                
                logger.info(f"User {username} ({user_id}) disconnected")
            else:
                logger.info(f"Unauthenticated connection {sid} disconnected")
        
        @self.sio.event
        async def authenticate(sid, data):
            """
            Authenticate socket connection with JWT token
            Expected data: {"token": "jwt_token"}
            """
            try:
                token = data.get('token')
                if not token:
                    await self.sio.emit('authentication_error', {
                        'message': 'Token is required'
                    }, room=sid)
                    await self.sio.disconnect(sid)
                    return
                
                # Verify token and get user info
                from app.core.security.jwt import decode_access_token
                from app.core.config.database import async_session_maker
                from sqlalchemy import select
                from app.db.models import User
                
                # Decode token
                payload = decode_access_token(token)
                if not payload:
                    await self.sio.emit('authentication_error', {
                        'message': 'Invalid or expired token'
                    }, room=sid)
                    await self.sio.disconnect(sid)
                    return
                
                user_id = UUID(payload.get('user_id'))
                token_version = payload.get('token_version', 0)
                
                # Verify token version (check if token is still valid)
                async with async_session_maker() as db:
                    result = await db.execute(select(User).where(User.id == user_id))
                    user = result.scalar_one_or_none()
                
                if not user or user.is_active != 1:
                    await self.sio.emit('authentication_error', {
                        'message': 'User not found or inactive'
                    }, room=sid)
                    await self.sio.disconnect(sid)
                    return
                
                if user.token_version != token_version:
                    await self.sio.emit('authentication_error', {
                        'message': 'Token has been invalidated'
                    }, room=sid)
                    await self.sio.disconnect(sid)
                    return
                
                # Store connection info
                self.connections[sid] = {
                    'user_id': user_id,
                    'tenant_id': user.tenant_id,
                    'username': user.username,
                    'connected_at': datetime.now(timezone.utc),
                    'role': user.role.name if user.role else None,
                    'level': user.level.name if user.level else None
                }
                
                # Add to user sessions
                if user_id not in self.user_sessions:
                    self.user_sessions[user_id] = set()
                self.user_sessions[user_id].add(sid)
                
                # Add to tenant sessions
                if user.tenant_id:
                    if user.tenant_id not in self.tenant_sessions:
                        self.tenant_sessions[user.tenant_id] = set()
                    self.tenant_sessions[user.tenant_id].add(sid)
                
                # Join user-specific room
                await self.sio.enter_room(sid, f"user:{user_id}")
                
                # Join tenant-specific room if tenant exists
                if user.tenant_id:
                    await self.sio.enter_room(sid, f"tenant:{user.tenant_id}")
                
                # Notify successful authentication
                await self.sio.emit('authenticated', {
                    'message': 'Successfully authenticated',
                    'user_id': str(user_id),
                    'username': user.username,
                    'timestamp': datetime.now(timezone.utc).isoformat()
                }, room=sid)
                
                logger.info(f"User {user.username} ({user_id}) authenticated on socket {sid}")
                
                # Send missed notifications
                try:
                    from app.services.v1.handle_notification import NotificationService
                    async with async_session_maker() as notification_db:
                        missed_count = await NotificationService.send_missed_notifications(user_id, notification_db)
                        if missed_count > 0:
                            await self.sio.emit('missed_notifications_sent', {
                                'count': missed_count,
                                'message': f'You have {missed_count} missed notification(s)'
                            }, room=sid)
                            logger.info(f"Sent {missed_count} missed notifications to user {user_id}")
                except Exception as e:
                    logger.error(f"Error sending missed notifications: {str(e)}")
                    
            except Exception as e:
                logger.error(f"Authentication error: {str(e)}")
                await self.sio.emit('authentication_error', {
                    'message': 'Authentication failed'
                }, room=sid)
                await self.sio.disconnect(sid)
        
        @self.sio.event
        async def ping(sid, data):
            """Handle ping from client"""
            await self.sio.emit('pong', {
                'timestamp': datetime.now(timezone.utc).isoformat()
            }, room=sid)
        
        @self.sio.event
        async def subscribe_to_channels(sid, data):
            """
            Subscribe to additional channels/rooms
            Expected data: {"channels": ["channel1", "channel2"]}
            """
            try:
                channels = data.get('channels', [])
                conn_info = self.connections.get(sid)
                
                if not conn_info:
                    await self.sio.emit('error', {
                        'message': 'Not authenticated'
                    }, room=sid)
                    return
                
                for channel in channels:
                    # Validate channel access based on user permissions
                    # For now, allow all authenticated users to subscribe
                    await self.sio.enter_room(sid, channel)
                    logger.info(f"User {conn_info['username']} joined channel: {channel}")
                
                await self.sio.emit('channels_subscribed', {
                    'channels': channels,
                    'timestamp': datetime.now(timezone.utc).isoformat()
                }, room=sid)
                
            except Exception as e:
                logger.error(f"Subscribe error: {str(e)}")
                await self.sio.emit('error', {
                    'message': 'Failed to subscribe to channels'
                }, room=sid)
    
    async def send_to_user(self, user_id: UUID, event: str, data: dict):
        """
        Send message to specific user (all their sessions)
        """
        try:
            room = f"user:{user_id}"
            await self.sio.emit(event, data, room=room)
            logger.info(f"Sent '{event}' to user {user_id}")
        except Exception as e:
            logger.error(f"Error sending to user {user_id}: {str(e)}")
    
    async def send_to_tenant(self, tenant_id: UUID, event: str, data: dict):
        """
        Send message to all users in a tenant
        """
        try:
            room = f"tenant:{tenant_id}"
            await self.sio.emit(event, data, room=room)
            logger.info(f"Sent '{event}' to tenant {tenant_id}")
        except Exception as e:
            logger.error(f"Error sending to tenant {tenant_id}: {str(e)}")
    
    async def broadcast(self, event: str, data: dict, skip_sid: Optional[str] = None):
        """
        Broadcast message to all connected clients
        """
        try:
            await self.sio.emit(event, data, skip_sid=skip_sid)
            logger.info(f"Broadcasted '{event}' to all clients")
        except Exception as e:
            logger.error(f"Error broadcasting: {str(e)}")
    
    async def send_to_channel(self, channel: str, event: str, data: dict):
        """
        Send message to specific channel/room
        """
        try:
            await self.sio.emit(event, data, room=channel)
            logger.info(f"Sent '{event}' to channel {channel}")
        except Exception as e:
            logger.error(f"Error sending to channel {channel}: {str(e)}")
    
    async def disconnect_user(self, user_id: UUID, reason: str = "User logged out"):
        """
        Disconnect all sessions of a specific user
        Useful when user is kicked or token invalidated
        """
        try:
            if user_id in self.user_sessions:
                sids = list(self.user_sessions[user_id])
                for sid in sids:
                    await self.sio.emit('force_disconnect', {
                        'reason': reason,
                        'timestamp': datetime.now(timezone.utc).isoformat()
                    }, room=sid)
                    await self.sio.disconnect(sid)
                logger.info(f"Disconnected all sessions for user {user_id}")
        except Exception as e:
            logger.error(f"Error disconnecting user {user_id}: {str(e)}")
    
    def get_connected_users(self) -> List[Dict]:
        """Get list of all connected users"""
        return list(self.connections.values())
    
    def get_user_connection_count(self, user_id: UUID) -> int:
        """Get number of active connections for a user"""
        return len(self.user_sessions.get(user_id, set()))
    
    def is_user_online(self, user_id: UUID) -> bool:
        """Check if user has any active connections"""
        return user_id in self.user_sessions and len(self.user_sessions[user_id]) > 0
    
    def get_tenant_connection_count(self, tenant_id: UUID) -> int:
        """Get number of active connections in a tenant"""
        return len(self.tenant_sessions.get(tenant_id, set()))

# Global socket manager instance
socket_manager = SocketManager()
