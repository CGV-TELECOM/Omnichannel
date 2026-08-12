from sqlalchemy.ext.asyncio import AsyncSession
from app.schemas.responses.api_response_rule import api_response, ResponseStatus, ResponseStatusCode
from app.db.models import TicketEvent, User, Tenant, Ticket
from sqlalchemy import select, func, or_, and_, update
from sqlalchemy.exc import SQLAlchemyError
from app.schemas.requests.ticket_event import (
    TicketEventCreate, 
    TicketEventUpdate, 
    TicketEventResponse,
    TicketEventFilter
)
from app.utils.helpers import is_platform_admin, isCheckMaxLevelTenant
from uuid import UUID
from typing import Optional, Dict, Any
from datetime import datetime
import json
import logging

logger = logging.getLogger(__name__)

def is_valid_uuid(value: str | UUID | None) -> bool:
    if not value:
        return False
    try:
        UUID(str(value))
        return True
    except ValueError:
        return False

async def get_ticket_events(
    db: AsyncSession,
    current_user: User,
    id: Optional[UUID] = None,
    ticket_id: Optional[UUID] = None,
    event_type: Optional[str] = None,
    actor_type: Optional[str] = None,
    actor_id: Optional[UUID] = None,
    page: int = 1,
    page_size: int = 10,
    sort_by: Optional[str] = None,
    sort_order: str = "desc",
    tenant_id: Optional[UUID] = None,
    from_date: Optional[datetime] = None,
    to_date: Optional[datetime] = None
):
    """
    Lấy danh sách ticket events với pagination, search và filtering
    """
    try:
        # Check permissions
        is_super_admin = await is_platform_admin(current_user, db)
        
        # Build base query
        query = select(TicketEvent)
        
        # Build filters
        filters = []
        
        # Filter by tenant_id
        if is_super_admin and tenant_id:
            filters.append(TicketEvent.tenant_id == tenant_id)
        elif not is_super_admin:
            filters.append(TicketEvent.tenant_id == current_user.tenant_id)
        
        # Filter by ID
        if id:
            filters.append(TicketEvent.id == id)
        
        # Filter by ticket_id
        if ticket_id:
            filters.append(TicketEvent.ticket_id == ticket_id)
        
        # Filter by event_type
        if event_type:
            filters.append(TicketEvent.event_type.ilike(f"%{event_type}%"))
        
        # Filter by actor_type
        if actor_type:
            filters.append(TicketEvent.actor_type.ilike(f"%{actor_type}%"))
        
        # Filter by actor_id
        if actor_id:
            filters.append(TicketEvent.actor_id == actor_id)
        
        # Filter by date range
        if from_date:
            filters.append(TicketEvent.created_at >= from_date)
        if to_date:
            filters.append(TicketEvent.created_at <= to_date)
        
        # Apply filters to query
        if filters:
            query = query.where(and_(*filters))
        
        # Count total records - Use select().where() instead of subquery
        count_query = select(func.count()).select_from(TicketEvent)
        if filters:
            count_query = count_query.where(and_(*filters))
        
        total_count_result = await db.execute(count_query)
        total_count = total_count_result.scalar()
        
        # Sorting
        if sort_by:
            column = getattr(TicketEvent, sort_by, None)
            if column:
                if sort_order.lower() == "desc":
                    query = query.order_by(column.desc())
                else:
                    query = query.order_by(column.asc())
        else:
            # Default sort by created_at desc
            query = query.order_by(TicketEvent.created_at.desc())
        
        # Pagination
        offset = (page - 1) * page_size
        query = query.offset(offset).limit(page_size)
        
        # Execute query
        result = await db.execute(query)
        ticket_events = result.scalars().all()

        actor_user_ids = [UUID(event.actor_id) for event in ticket_events if is_valid_uuid(event.actor_id)]
        user_map = {}

        if actor_user_ids:
            user_result = await db.execute(
                select(User).where(User.id.in_(actor_user_ids))
            )
            users = user_result.scalars().all()
            user_map = {user.id: user for user in users}

        # Convert to response format
        ticket_event_list = []
        for event in ticket_events:
            actor_user = None

            if is_valid_uuid(event.actor_id):
                actor_user = user_map.get(UUID(event.actor_id))

            event_dict = {
                "id": event.id,
                "ticket_id": event.ticket_id,
                "event_type": event.event_type,
                "payload": json.loads(event.payload.decode("utf-8")) if event.payload else None,
                "actor_type": event.actor_type,
                "actor_id": event.actor_id,
                "actor_username": actor_user.username if actor_user else event.actor_id,
                "created_at": event.created_at,
                "tenant_id": event.tenant_id,
            }
            ticket_event_list.append(event_dict)
        
        total_pages = (total_count + page_size - 1) // page_size
        
        return api_response(
            status=ResponseStatus.SUCCESS,
            status_code=ResponseStatusCode.OK,
            message="Lấy danh sách ticket events thành công",
            data={
                "ticket_events": ticket_event_list,
                "pagination": {
                    "current_page": page,
                    "page_size": page_size,
                    "total_count": total_count,
                    "total_pages": total_pages
                }
            }
        )
    except SQLAlchemyError as e:
        logger.error(f"Database error in get_ticket_events: {str(e)}")
        return api_response(
            status=ResponseStatus.ERROR,
            status_code=ResponseStatusCode.INTERNAL_SERVER_ERROR,
            message="Lỗi truy vấn cơ sở dữ liệu",
            data=str(e)
        )
    except Exception as e:
        logger.error(f"Unexpected error in get_ticket_events: {str(e)}")
        return api_response(
            status=ResponseStatus.ERROR,
            status_code=ResponseStatusCode.INTERNAL_SERVER_ERROR,
            message="Đã xảy ra lỗi không mong muốn",
            data=str(e)
        )


async def get_ticket_event_by_id(event_id: UUID, db: AsyncSession, current_user: User):
    """
    Lấy thông tin chi tiết một ticket event theo ID
    """
    try:
        is_super_admin = await is_platform_admin(current_user, db)
        
        query = select(TicketEvent).where(TicketEvent.id == event_id)
        
        if not is_super_admin:
            query = query.where(TicketEvent.tenant_id == current_user.tenant_id)
        
        result = await db.execute(query)
        event = result.scalar_one_or_none()
        
        if not event:
            return api_response(
                status=ResponseStatus.ERROR,
                status_code=ResponseStatusCode.NOT_FOUND,
                message="Không tìm thấy ticket event"
            )
        
        event_data = {
            "id": event.id,
            "ticket_id": event.ticket_id,
            "event_type": event.event_type,
            "payload": json.loads(event.payload.decode('utf-8')) if event.payload else None,
            "actor_type": event.actor_type,
            "actor_id": event.actor_id,
            "created_at": event.created_at,
            "tenant_id": event.tenant_id
        }
        
        return api_response(
            status=ResponseStatus.SUCCESS,
            status_code=ResponseStatusCode.OK,
            message="Lấy thông tin ticket event thành công",
            data=event_data
        )
    except SQLAlchemyError as e:
        logger.error(f"Database error in get_ticket_event_by_id: {str(e)}")
        return api_response(
            status=ResponseStatus.ERROR,
            status_code=ResponseStatusCode.INTERNAL_SERVER_ERROR,
            message="Lỗi truy vấn cơ sở dữ liệu",
            data=str(e)
        )
    except Exception as e:
        logger.error(f"Unexpected error in get_ticket_event_by_id: {str(e)}")
        return api_response(
            status=ResponseStatus.ERROR,
            status_code=ResponseStatusCode.INTERNAL_SERVER_ERROR,
            message="Đã xảy ra lỗi không mong muốn",
            data=str(e)
        )


async def create_ticket_event(event_data: TicketEventCreate, db: AsyncSession, current_user: User):
    """
    Tạo ticket event mới
    """
    try:
        is_super_admin = await is_platform_admin(current_user, db)
        
        # Validate ticket exists and user has access
        ticket_query = select(Ticket).where(Ticket.id == event_data.ticket_id)
        if not is_super_admin:
            ticket_query = ticket_query.where(Ticket.tenant_id == current_user.tenant_id)
        
        ticket_result = await db.execute(ticket_query)
        ticket = ticket_result.scalar_one_or_none()
        
        if not ticket:
            return api_response(
                status=ResponseStatus.ERROR,
                status_code=ResponseStatusCode.NOT_FOUND,
                message="Không tìm thấy ticket hoặc bạn không có quyền truy cập"
            )
        
        # Set tenant_id
        if is_super_admin and event_data.tenant_id:
            # Validate tenant exists
            tenant_check = await db.execute(select(Tenant).where(Tenant.id == event_data.tenant_id))
            if not tenant_check.scalar_one_or_none():
                return api_response(
                    status=ResponseStatus.ERROR,
                    status_code=ResponseStatusCode.NOT_FOUND,
                    message="Tenant không tồn tại"
                )
            tenant_id = event_data.tenant_id
        else:
            tenant_id = current_user.tenant_id
        
        # Convert payload dict to binary JSON
        payload_binary = None
        if event_data.payload:
            payload_binary = json.dumps(event_data.payload).encode('utf-8')
        
        # Get actor role name - Tự động lấy tên role của user
        actor_role_name = None
        if current_user.role:
            actor_role_name = current_user.role.name
        
        # Get actor_id as string
        actor_id_str = event_data.actor_id if event_data.actor_id else str(current_user.id)
        
        # Create new ticket event
        new_event = TicketEvent(
            ticket_id=event_data.ticket_id,
            event_type=event_data.event_type,
            payload=payload_binary,
            actor_type=actor_role_name,  # Gán tên role của user
            actor_id=actor_id_str,  # String để linh hoạt (UUID as string, 'system', 'api', etc.)
            tenant_id=tenant_id
        )
        
        db.add(new_event)
        await db.commit()
        await db.refresh(new_event)
        
        event_response = {
            "id": new_event.id,
            "ticket_id": new_event.ticket_id,
            "event_type": new_event.event_type,
            "payload": json.loads(new_event.payload.decode('utf-8')) if new_event.payload else None,
            "actor_type": new_event.actor_type,
            "actor_id": new_event.actor_id,
            "created_at": new_event.created_at,
            "tenant_id": new_event.tenant_id
        }
        
        return api_response(
            status=ResponseStatus.SUCCESS,
            status_code=ResponseStatusCode.CREATED,
            message="Tạo ticket event thành công",
            data=event_response
        )
    except SQLAlchemyError as e:
        await db.rollback()
        logger.error(f"Database error in create_ticket_event: {str(e)}")
        return api_response(
            status=ResponseStatus.ERROR,
            status_code=ResponseStatusCode.INTERNAL_SERVER_ERROR,
            message="Lỗi khi tạo ticket event",
            data=str(e)
        )
    except Exception as e:
        await db.rollback()
        logger.error(f"Unexpected error in create_ticket_event: {str(e)}")
        return api_response(
            status=ResponseStatus.ERROR,
            status_code=ResponseStatusCode.INTERNAL_SERVER_ERROR,
            message="Đã xảy ra lỗi không mong muốn",
            data=str(e)
        )


async def update_ticket_event(event_id: UUID, event_data: TicketEventUpdate, db: AsyncSession, current_user: User):
    """
    Cập nhật ticket event
    """
    try:
        is_super_admin = await is_platform_admin(current_user, db)
        
        # Check if event exists and user has permission
        query = select(TicketEvent).where(TicketEvent.id == event_id)
        if not is_super_admin:
            query = query.where(TicketEvent.tenant_id == current_user.tenant_id)
        
        result = await db.execute(query)
        event = result.scalar_one_or_none()
        
        if not event:
            return api_response(
                status=ResponseStatus.ERROR,
                status_code=ResponseStatusCode.NOT_FOUND,
                message="Không tìm thấy ticket event hoặc bạn không có quyền truy cập"
            )
        
        # Update fields
        update_data = event_data.model_dump(exclude_unset=True)
        
        # Convert payload if provided
        if 'payload' in update_data and update_data['payload'] is not None:
            update_data['payload'] = json.dumps(update_data['payload']).encode('utf-8')
        
        # Note: actor_type không được cập nhật vì nó tự động gán từ role của user khi tạo
        for key, value in update_data.items():
            setattr(event, key, value)
        
        await db.commit()
        await db.refresh(event)
        
        event_response = {
            "id": event.id,
            "ticket_id": event.ticket_id,
            "event_type": event.event_type,
            "payload": json.loads(event.payload.decode('utf-8')) if event.payload else None,
            "actor_type": event.actor_type,
            "actor_id": event.actor_id,
            "created_at": event.created_at,
            "tenant_id": event.tenant_id
        }
        
        return api_response(
            status=ResponseStatus.SUCCESS,
            status_code=ResponseStatusCode.OK,
            message="Cập nhật ticket event thành công",
            data=event_response
        )
    except SQLAlchemyError as e:
        await db.rollback()
        logger.error(f"Database error in update_ticket_event: {str(e)}")
        return api_response(
            status=ResponseStatus.ERROR,
            status_code=ResponseStatusCode.INTERNAL_SERVER_ERROR,
            message="Lỗi khi cập nhật ticket event",
            data=str(e)
        )
    except Exception as e:
        await db.rollback()
        logger.error(f"Unexpected error in update_ticket_event: {str(e)}")
        return api_response(
            status=ResponseStatus.ERROR,
            status_code=ResponseStatusCode.INTERNAL_SERVER_ERROR,
            message="Đã xảy ra lỗi không mong muốn",
            data=str(e)
        )


async def delete_ticket_event(event_id: UUID, db: AsyncSession, current_user: User):
    """
    Xóa ticket event (hard delete)
    """
    try:
        is_super_admin = await is_platform_admin(current_user, db)
        
        # Check if event exists and user has permission
        query = select(TicketEvent).where(TicketEvent.id == event_id)
        if not is_super_admin:
            query = query.where(TicketEvent.tenant_id == current_user.tenant_id)
        
        result = await db.execute(query)
        event = result.scalar_one_or_none()
        
        if not event:
            return api_response(
                status=ResponseStatus.ERROR,
                status_code=ResponseStatusCode.NOT_FOUND,
                message="Không tìm thấy ticket event hoặc bạn không có quyền truy cập"
            )
        
        await db.delete(event)
        await db.commit()
        
        return api_response(
            status=ResponseStatus.SUCCESS,
            status_code=ResponseStatusCode.OK,
            message="Xóa ticket event thành công"
        )
    except SQLAlchemyError as e:
        await db.rollback()
        logger.error(f"Database error in delete_ticket_event: {str(e)}")
        return api_response(
            status=ResponseStatus.ERROR,
            status_code=ResponseStatusCode.INTERNAL_SERVER_ERROR,
            message="Lỗi khi xóa ticket event",
            data=str(e)
        )
    except Exception as e:
        await db.rollback()
        logger.error(f"Unexpected error in delete_ticket_event: {str(e)}")
        return api_response(
            status=ResponseStatus.ERROR,
            status_code=ResponseStatusCode.INTERNAL_SERVER_ERROR,
            message="Đã xảy ra lỗi không mong muốn",
            data=str(e)
        )
