from sqlalchemy.ext.asyncio import AsyncSession
from app.schemas.responses.api_response_rule import api_response, ResponseStatus, ResponseStatusCode
from app.db.models import Ticket, User, TicketTemplate, TicketFlow, Tag, TicketExtension, TicketEvent, TicketStatus, Role, ticket_tag_association
from sqlalchemy import select, func, or_, and_, update as sql_update, insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import selectinload
from app.schemas.requests.ticket import (
    TicketCreate,
    TicketUpdate,
    TicketAssign,
    TicketStatusUpdate,
    TicketResponse
)
from app.utils.helpers import isCheckMaxLevel
from uuid import UUID
from typing import Optional, Dict, Any, List
from datetime import datetime, timezone
import json
import logging

logger = logging.getLogger(__name__)


async def generate_ticket_code(db: AsyncSession, tenant_id: UUID) -> str:
    """
    Tự động tạo mã ticket theo format: TKT-YYYY-XXXX
    XXXX là số tăng dần theo năm
    """
    try:
        current_year = datetime.now(timezone.utc).year
        
        # Đếm số ticket của tenant trong năm hiện tại
        count_query = select(func.count(Ticket.id)).where(
            and_(
                Ticket.tenant_id == tenant_id,
                func.extract('year', Ticket.created_at) == current_year
            )
        )
        count = await db.scalar(count_query) or 0
        
        # Tạo code mới
        next_number = count + 1
        code = f"TKT-{current_year}-{next_number:04d}"
        
        return code
    except Exception as e:
        logger.error(f"Error generating ticket code: {str(e)}")
        # Fallback: sử dụng timestamp
        return f"TKT-{current_year}-{int(datetime.now(timezone.utc).timestamp())}"


async def get_user_role_name(user: User, db: AsyncSession) -> str:
    """Helper để lấy role name của user"""
    try:
        if user.role_id:
            role_query = select(Role).where(Role.id == user.role_id)
            role_result = await db.execute(role_query)
            role = role_result.scalar_one_or_none()
            return role.name if role else "user"
        return "user"
    except Exception as e:
        logger.error(f"Error getting user role: {str(e)}")
        return "user"


async def log_ticket_event(
    db: AsyncSession,
    ticket_id: UUID,
    event_type: str,
    actor_id: str,
    actor_type: str,
    payload: Optional[Dict[str, Any]] = None,
    tenant_id: Optional[UUID] = None
):
    """Helper function để log ticket events"""
    try:
        event = TicketEvent(
            ticket_id=ticket_id,
            event_type=event_type,
            payload=json.dumps(payload).encode('utf-8') if payload else None,
            actor_id=actor_id,
            actor_type=actor_type,
            tenant_id=tenant_id
        )
        db.add(event)
        await db.flush()
    except Exception as e:
        logger.error(f"Error logging ticket event: {str(e)}")


async def get_tickets(
    db: AsyncSession,
    current_user: User,
    id: Optional[UUID] = None,
    code: Optional[str] = None,
    status: Optional[str] = None,
    priority: Optional[str] = None,
    template_id: Optional[UUID] = None,
    flow_id: Optional[UUID] = None,
    created_by: Optional[UUID] = None,
    assigned_to: Optional[UUID] = None,
    tag_ids: Optional[List[UUID]] = None,
    page: int = 1,
    page_size: int = 10,
    search: Optional[str] = None,
    sort_by: Optional[str] = None,
    sort_order: str = "desc",
    tenant_id: Optional[UUID] = None
):
    """
    Lấy danh sách tickets với pagination, search và filtering
    """
    try:
        # Check permissions
        is_super_admin = await isCheckMaxLevel(current_user, db)
        
        # Build base query với relationships
        query = select(Ticket).options(
            selectinload(Ticket.template),
            selectinload(Ticket.flow),
            selectinload(Ticket.tags),
            selectinload(Ticket.extensions)
        )
        
        # Build filters
        filters = []
        
        # Filter by tenant_id
        if is_super_admin and tenant_id:
            filters.append(Ticket.tenant_id == tenant_id)
        elif not is_super_admin:
            filters.append(Ticket.tenant_id == current_user.tenant_id)
        
        # Filter by id
        if id:
            filters.append(Ticket.id == id)
        
        # Filter by code
        if code:
            filters.append(Ticket.code.ilike(f"%{code}%"))
        
        # Filter by status
        if status:
            filters.append(Ticket.status == status)
        
        # Filter by priority
        if priority:
            filters.append(Ticket.priority == priority)
        
        # Filter by template_id
        if template_id:
            filters.append(Ticket.template_id == template_id)
        
        # Filter by flow_id
        if flow_id:
            filters.append(Ticket.flow_id == flow_id)
        
        # Filter by created_by
        if created_by:
            filters.append(Ticket.created_by == created_by)
        
        # Filter by assigned_to
        if assigned_to:
            filters.append(Ticket.assigned_to == assigned_to)
        
        # Search in title and description
        if search:
            search_filter = or_(
                Ticket.title.ilike(f"%{search}%"),
                Ticket.description.ilike(f"%{search}%"),
                Ticket.code.ilike(f"%{search}%")
            )
            filters.append(search_filter)
        
        # Apply filters
        if filters:
            query = query.where(and_(*filters))
        
        # Filter by tags if specified
        if tag_ids:
            query = query.join(Ticket.tags).where(Tag.id.in_(tag_ids))
        
        # Count total
        count_query = select(func.count()).select_from(query.subquery())
        total = await db.scalar(count_query) or 0
        
        # Sorting
        if sort_by:
            sort_column = getattr(Ticket, sort_by, Ticket.created_at)
            if sort_order.lower() == "asc":
                query = query.order_by(sort_column.asc())
            else:
                query = query.order_by(sort_column.desc())
        else:
            query = query.order_by(Ticket.created_at.desc())
        
        # Pagination
        query = query.offset((page - 1) * page_size).limit(page_size)
        
        # Execute query
        result = await db.execute(query)
        tickets = result.scalars().all()
        
        # Get user names for created_by and assigned_to
        user_ids = set()
        for ticket in tickets:
            user_ids.add(ticket.created_by)
            if ticket.assigned_to:
                user_ids.add(ticket.assigned_to)
        
        user_dict = {}
        if user_ids:
            user_query = select(User).where(User.id.in_(user_ids))
            user_result = await db.execute(user_query)
            users = user_result.scalars().all()
            user_dict = {user.id: user.fullname or user.username for user in users}
        
        # Build response
        ticket_responses = []
        for ticket in tickets:
            ticket_data = TicketResponse(
                id=ticket.id,
                tenant_id=ticket.tenant_id,
                code=ticket.code,
                title=ticket.title,
                description=ticket.description,
                status=ticket.status,
                priority=ticket.priority,
                template_id=ticket.template_id,
                flow_id=ticket.flow_id,
                sla_id=ticket.sla_id,
                created_by=ticket.created_by,
                assigned_to=ticket.assigned_to,
                created_at=ticket.created_at,
                closed_at=ticket.closed_at,
                template_name=ticket.template.name if ticket.template else None,
                flow_name=ticket.flow.name if ticket.flow else None,
                created_by_name=user_dict.get(ticket.created_by),
                assigned_to_name=user_dict.get(ticket.assigned_to) if ticket.assigned_to else None,
                tags=[{"id": str(tag.id), "name": tag.name, "color": tag.color} for tag in ticket.tags] if ticket.tags else [],
                extension_data=json.loads(ticket.extensions.data.decode('utf-8')) if ticket.extensions and ticket.extensions.data else None
            )
            ticket_responses.append(ticket_data)
        
        total_pages = (total + page_size - 1) // page_size
        
        return api_response(
            status=ResponseStatus.SUCCESS,
            message="Lấy danh sách tickets thành công",
            data={
                "items": ticket_responses,
                "total": total,
                "page": page,
                "page_size": page_size,
                "total_pages": total_pages
            },
            status_code=ResponseStatusCode.OK
        )
    except SQLAlchemyError as e:
        await db.rollback()
        logger.error(f"Database error in get_tickets: {str(e)}")
        return api_response(
            status=ResponseStatus.ERROR,
            message="Lỗi truy vấn cơ sở dữ liệu",
            data=str(e),
            status_code=ResponseStatusCode.INTERNAL_SERVER_ERROR
        )
    except Exception as e:
        logger.error(f"Error in get_tickets: {str(e)}")
        return api_response(
            status=ResponseStatus.ERROR,
            message="Đã xảy ra lỗi không mong muốn từ Server hãy thử lại sau",
            data=str(e),
            status_code=ResponseStatusCode.INTERNAL_SERVER_ERROR
        )


async def get_ticket_by_id(ticket_id: UUID, db: AsyncSession, current_user: User):
    """Lấy thông tin ticket theo ID"""
    try:
        is_super_admin = await isCheckMaxLevel(current_user, db)
        
        query = select(Ticket).options(
            selectinload(Ticket.template),
            selectinload(Ticket.flow),
            selectinload(Ticket.tags),
            selectinload(Ticket.extensions)
        ).where(Ticket.id == ticket_id)
        
        if not is_super_admin:
            query = query.where(Ticket.tenant_id == current_user.tenant_id)
        
        result = await db.execute(query)
        ticket = result.scalar_one_or_none()
        
        if not ticket:
            return api_response(
                status=ResponseStatus.ERROR,
                message="Ticket không tồn tại",
                data=None,
                status_code=ResponseStatusCode.NOT_FOUND
            )
        
        # Get user names
        user_ids = [ticket.created_by]
        if ticket.assigned_to:
            user_ids.append(ticket.assigned_to)
        
        user_query = select(User).where(User.id.in_(user_ids))
        user_result = await db.execute(user_query)
        users = user_result.scalars().all()
        user_dict = {user.id: user.fullname or user.username for user in users}
        
        ticket_data = TicketResponse(
            id=ticket.id,
            tenant_id=ticket.tenant_id,
            code=ticket.code,
            title=ticket.title,
            description=ticket.description,
            status=ticket.status,
            priority=ticket.priority,
            template_id=ticket.template_id,
            flow_id=ticket.flow_id,
            sla_id=ticket.sla_id,
            created_by=ticket.created_by,
            assigned_to=ticket.assigned_to,
            created_at=ticket.created_at,
            closed_at=ticket.closed_at,
            template_name=ticket.template.name if ticket.template else None,
            flow_name=ticket.flow.name if ticket.flow else None,
            created_by_name=user_dict.get(ticket.created_by),
            assigned_to_name=user_dict.get(ticket.assigned_to) if ticket.assigned_to else None,
            tags=[{"id": str(tag.id), "name": tag.name, "color": tag.color} for tag in ticket.tags] if ticket.tags else [],
            extension_data=json.loads(ticket.extensions.data.decode('utf-8')) if ticket.extensions and ticket.extensions.data else None
        )
        
        return api_response(
            status=ResponseStatus.SUCCESS,
            message="Lấy thông tin ticket thành công",
            data=ticket_data,
            status_code=ResponseStatusCode.OK
        )
    except SQLAlchemyError as e:
        logger.error(f"Database error in get_ticket_by_id: {str(e)}")
        return api_response(
            status=ResponseStatus.ERROR,
            message="Lỗi truy vấn cơ sở dữ liệu",
            data=str(e),
            status_code=ResponseStatusCode.INTERNAL_SERVER_ERROR
        )
    except Exception as e:
        logger.error(f"Error in get_ticket_by_id: {str(e)}")
        return api_response(
            status=ResponseStatus.ERROR,
            message="Đã xảy ra lỗi không mong muốn từ Server hãy thử lại sau",
            data=str(e),
            status_code=ResponseStatusCode.INTERNAL_SERVER_ERROR
        )


async def get_ticket_by_code(code: str, db: AsyncSession, current_user: User):
    """Lấy thông tin ticket theo code"""
    try:
        is_super_admin = await isCheckMaxLevel(current_user, db)
        
        query = select(Ticket).options(
            selectinload(Ticket.template),
            selectinload(Ticket.flow),
            selectinload(Ticket.tags),
            selectinload(Ticket.extensions)
        ).where(Ticket.code == code)
        
        if not is_super_admin:
            query = query.where(Ticket.tenant_id == current_user.tenant_id)
        
        result = await db.execute(query)
        ticket = result.scalar_one_or_none()
        
        if not ticket:
            return api_response(
                status=ResponseStatus.ERROR,
                message="Ticket không tồn tại",
                data=None,
                status_code=ResponseStatusCode.NOT_FOUND
            )
        
        # Get user names
        user_ids = [ticket.created_by]
        if ticket.assigned_to:
            user_ids.append(ticket.assigned_to)
        
        user_query = select(User).where(User.id.in_(user_ids))
        user_result = await db.execute(user_query)
        users = user_result.scalars().all()
        user_dict = {user.id: user.fullname or user.username for user in users}
        
        ticket_data = TicketResponse(
            id=ticket.id,
            tenant_id=ticket.tenant_id,
            code=ticket.code,
            title=ticket.title,
            description=ticket.description,
            status=ticket.status,
            priority=ticket.priority,
            template_id=ticket.template_id,
            flow_id=ticket.flow_id,
            sla_id=ticket.sla_id,
            created_by=ticket.created_by,
            assigned_to=ticket.assigned_to,
            created_at=ticket.created_at,
            closed_at=ticket.closed_at,
            template_name=ticket.template.name if ticket.template else None,
            flow_name=ticket.flow.name if ticket.flow else None,
            created_by_name=user_dict.get(ticket.created_by),
            assigned_to_name=user_dict.get(ticket.assigned_to) if ticket.assigned_to else None,
            tags=[{"id": str(tag.id), "name": tag.name, "color": tag.color} for tag in ticket.tags] if ticket.tags else [],
            extension_data=json.loads(ticket.extensions.data.decode('utf-8')) if ticket.extensions and ticket.extensions.data else None
        )
        
        return api_response(
            status=ResponseStatus.SUCCESS,
            message="Lấy thông tin ticket thành công",
            data=ticket_data,
            status_code=ResponseStatusCode.OK
        )
    except SQLAlchemyError as e:
        logger.error(f"Database error in get_ticket_by_code: {str(e)}")
        return api_response(
            status=ResponseStatus.ERROR,
            message="Lỗi truy vấn cơ sở dữ liệu",
            data=str(e),
            status_code=ResponseStatusCode.INTERNAL_SERVER_ERROR
        )
    except Exception as e:
        logger.error(f"Error in get_ticket_by_code: {str(e)}")
        return api_response(
            status=ResponseStatus.ERROR,
            message="Đã xảy ra lỗi không mong muốn từ Server hãy thử lại sau",
            data=str(e),
            status_code=ResponseStatusCode.INTERNAL_SERVER_ERROR
        )


async def create_ticket(ticket_data: TicketCreate, db: AsyncSession, current_user: User):
    """Tạo ticket mới"""
    try:
        is_super_admin = await isCheckMaxLevel(current_user, db)
        
        # Get user role name for logging
        actor_type = await get_user_role_name(current_user, db)
        
        # Determine tenant_id
        if is_super_admin and ticket_data.tenant_id:
            tenant_id = ticket_data.tenant_id
        else:
            tenant_id = current_user.tenant_id
        
        # Validate template if provided
        if ticket_data.template_id:
            template_query = select(TicketTemplate).where(
                and_(
                    TicketTemplate.id == ticket_data.template_id,
                    TicketTemplate.tenant_id == tenant_id,
                    TicketTemplate.is_active == True
                )
            )
            template_result = await db.execute(template_query)
            template = template_result.scalar_one_or_none()
            if not template:
                return api_response(
                    status=ResponseStatus.ERROR,
                    message="Template không tồn tại hoặc không hoạt động",
                    data=None,
                    status_code=ResponseStatusCode.BAD_REQUEST
                )
        
        # Validate flow if provided
        if ticket_data.flow_id:
            flow_query = select(TicketFlow).where(
                and_(
                    TicketFlow.id == ticket_data.flow_id,
                    TicketFlow.tenant_id == tenant_id
                )
            )
            flow_result = await db.execute(flow_query)
            flow = flow_result.scalar_one_or_none()
            if not flow:
                return api_response(
                    status=ResponseStatus.ERROR,
                    message="Flow không tồn tại",
                    data=None,
                    status_code=ResponseStatusCode.BAD_REQUEST
                )
        
        # Validate assigned_to if provided
        if ticket_data.assigned_to:
            user_query = select(User).where(
                and_(
                    User.id == ticket_data.assigned_to,
                    User.tenant_id == tenant_id,
                    User.is_active == 1
                )
            )
            user_result = await db.execute(user_query)
            assigned_user = user_result.scalar_one_or_none()
            if not assigned_user:
                return api_response(
                    status=ResponseStatus.ERROR,
                    message="User được gán không tồn tại hoặc không hoạt động",
                    data=None,
                    status_code=ResponseStatusCode.BAD_REQUEST
                )
        
        # Generate ticket code
        code = await generate_ticket_code(db, tenant_id)
        
        # Create ticket
        new_ticket = Ticket(
            tenant_id=tenant_id,
            code=code,
            title=ticket_data.title,
            description=ticket_data.description,
            priority=ticket_data.priority or "medium",
            status=TicketStatus.PENDING,
            template_id=ticket_data.template_id,
            flow_id=ticket_data.flow_id,
            sla_id=ticket_data.sla_id,
            created_by=current_user.id,
            assigned_to=ticket_data.assigned_to
        )
        db.add(new_ticket)
        await db.flush()
        
        # Add extension data if provided
        if ticket_data.extension_data:
            extension = TicketExtension(
                ticket_id=new_ticket.id,
                data=json.dumps(ticket_data.extension_data).encode('utf-8')
            )
            db.add(extension)
        
        # Add tags if provided (using association table directly to avoid lazy loading)
        if ticket_data.tag_ids:
            # Validate tags exist
            tag_query = select(Tag.id).where(
                and_(
                    Tag.id.in_(ticket_data.tag_ids),
                    Tag.tenant_id == tenant_id
                )
            )
            tag_result = await db.execute(tag_query)
            valid_tag_ids = [row[0] for row in tag_result.all()]
            
            # Insert into association table directly
            for tag_id in valid_tag_ids:
                stmt = insert(ticket_tag_association).values(
                    ticket_id=new_ticket.id,
                    tag_id=tag_id
                )
                await db.execute(stmt)
        
        # Log event
        await log_ticket_event(
            db=db,
            ticket_id=new_ticket.id,
            event_type="CREATED",
            actor_id=str(current_user.id),
            actor_type=actor_type,
            payload={"title": ticket_data.title, "priority": ticket_data.priority},
            tenant_id=tenant_id
        )
        
        await db.commit()
        await db.refresh(new_ticket)
        
        return api_response(
            status=ResponseStatus.SUCCESS,
            message="Tạo ticket thành công",
            data={"id": str(new_ticket.id), "code": new_ticket.code},
            status_code=ResponseStatusCode.CREATED
        )
    except SQLAlchemyError as e:
        await db.rollback()
        logger.error(f"Database error in create_ticket: {str(e)}")
        return api_response(
            status=ResponseStatus.ERROR,
            message="Lỗi truy vấn cơ sở dữ liệu",
            data=str(e),
            status_code=ResponseStatusCode.INTERNAL_SERVER_ERROR
        )
    except Exception as e:
        await db.rollback()
        logger.error(f"Error in create_ticket: {str(e)}")
        return api_response(
            status=ResponseStatus.ERROR,
            message="Đã xảy ra lỗi không mong muốn từ Server hãy thử lại sau",
            data=str(e),
            status_code=ResponseStatusCode.INTERNAL_SERVER_ERROR
        )


async def update_ticket(ticket_id: UUID, ticket_data: TicketUpdate, db: AsyncSession, current_user: User):
    """Cập nhật thông tin ticket"""
    try:
        is_super_admin = await isCheckMaxLevel(current_user, db)
        actor_type = await get_user_role_name(current_user, db)
        
        # Get ticket
        query = select(Ticket).where(Ticket.id == ticket_id)
        if not is_super_admin:
            query = query.where(Ticket.tenant_id == current_user.tenant_id)
        
        result = await db.execute(query)
        ticket = result.scalar_one_or_none()
        
        if not ticket:
            return api_response(
                status=ResponseStatus.ERROR,
                message="Ticket không tồn tại",
                data=None,
                status_code=ResponseStatusCode.NOT_FOUND
            )
        
        # Track changes for event log
        changes = {}
        
        # Update fields
        if ticket_data.title is not None:
            if ticket.title != ticket_data.title:
                changes["title"] = {"old": ticket.title, "new": ticket_data.title}
                ticket.title = ticket_data.title
        
        if ticket_data.description is not None:
            if ticket.description != ticket_data.description:
                changes["description"] = {"old": ticket.description, "new": ticket_data.description}
                ticket.description = ticket_data.description
        
        if ticket_data.status is not None:
            if ticket.status != ticket_data.status:
                changes["status"] = {"old": ticket.status.value, "new": ticket_data.status.value}
                ticket.status = ticket_data.status
                # Set closed_at if status is closed
                if ticket_data.status == TicketStatus.CLOSED:
                    ticket.closed_at = datetime.now(timezone.utc)
        
        if ticket_data.priority is not None:
            if ticket.priority != ticket_data.priority:
                changes["priority"] = {"old": ticket.priority.value, "new": ticket_data.priority.value}
                ticket.priority = ticket_data.priority
        
        if ticket_data.assigned_to is not None:
            if ticket.assigned_to != ticket_data.assigned_to:
                # Validate assigned_to user
                user_query = select(User).where(
                    and_(
                        User.id == ticket_data.assigned_to,
                        User.tenant_id == ticket.tenant_id,
                        User.is_active == 1
                    )
                )
                user_result = await db.execute(user_query)
                assigned_user = user_result.scalar_one_or_none()
                if not assigned_user:
                    return api_response(
                        status=ResponseStatus.ERROR,
                        message="User được gán không tồn tại hoặc không hoạt động",
                        data=None,
                        status_code=ResponseStatusCode.BAD_REQUEST
                    )
                changes["assigned_to"] = {"old": str(ticket.assigned_to) if ticket.assigned_to else None, "new": str(ticket_data.assigned_to)}
                ticket.assigned_to = ticket_data.assigned_to
        
        if ticket_data.sla_id is not None:
            if ticket.sla_id != ticket_data.sla_id:
                changes["sla_id"] = {"old": str(ticket.sla_id) if ticket.sla_id else None, "new": str(ticket_data.sla_id)}
                ticket.sla_id = ticket_data.sla_id

        # Update template_id if provided
        if ticket_data.template_id is not None:
            if ticket.template_id != ticket_data.template_id:
                # Validate template
                template_query = select(TicketTemplate).where(
                    and_(
                        TicketTemplate.id == ticket_data.template_id,
                        TicketTemplate.tenant_id == ticket.tenant_id,
                        TicketTemplate.is_active.is_(True)                    
                    )
                )
                template_result = await db.execute(template_query)
                template = template_result.scalar_one_or_none()

                if not template:
                    return api_response(
                        status=ResponseStatus.ERROR,
                        message="Template không tồn tại hoặc không hoạt động",
                        data=None,
                        status_code=ResponseStatusCode.BAD_REQUEST
                    )

                changes["template_id"] = {
                    "old": str(ticket.template_id) if ticket.template_id else None,
                    "new": str(ticket_data.template_id)
                }

                ticket.template_id = ticket_data.template_id
        
        # Update extension data if provided
        if ticket_data.extension_data is not None:
            extension_query = select(TicketExtension).where(TicketExtension.ticket_id == ticket_id)
            extension_result = await db.execute(extension_query)
            extension = extension_result.scalar_one_or_none()
            
            if extension:
                extension.data = json.dumps(ticket_data.extension_data).encode('utf-8')
            else:
                new_extension = TicketExtension(
                    ticket_id=ticket_id,
                    data=json.dumps(ticket_data.extension_data).encode('utf-8')
                )
                db.add(new_extension)
            changes["extension_data"] = "updated"
        
        # Update tags if provided
        if ticket_data.tag_ids is not None:
            tag_query = select(Tag.id).where(
                and_(
                    Tag.id.in_(ticket_data.tag_ids),
                    Tag.tenant_id == ticket.tenant_id
                )
            )

            tag_result = await db.execute(tag_query)
            valid_tag_ids = tag_result.scalars().all()
            
            # Delete existing tags and add new ones using association table
            from sqlalchemy import delete as sql_delete
            delete_stmt = sql_delete(ticket_tag_association).where(
                ticket_tag_association.c.ticket_id == ticket_id
            )
            await db.execute(delete_stmt)
            
            # Insert new associations
            for tag_id in valid_tag_ids:
                stmt = insert(ticket_tag_association).values(
                    ticket_id=ticket_id,
                    tag_id=tag_id
                )
                await db.execute(stmt)
            
            changes["tags"] = "updated"
        
        # Log event if there are changes
        if changes:
            await log_ticket_event(
                db=db,
                ticket_id=ticket_id,
                event_type="UPDATED",
                actor_id=str(current_user.id),
                actor_type=actor_type,
                payload=changes,
                tenant_id=ticket.tenant_id
            )
        
        await db.commit()
        
        return api_response(
            status=ResponseStatus.SUCCESS,
            message="Cập nhật ticket thành công",
            data={"id": str(ticket.id), "code": ticket.code},
            status_code=ResponseStatusCode.OK
        )
    except SQLAlchemyError as e:
        await db.rollback()
        logger.error(f"Database error in update_ticket: {str(e)}")
        return api_response(
            status=ResponseStatus.ERROR,
            message="Lỗi truy vấn cơ sở dữ liệu",
            data=str(e),
            status_code=ResponseStatusCode.INTERNAL_SERVER_ERROR
        )
    except Exception as e:
        await db.rollback()
        logger.error(f"Error in update_ticket: {str(e)}")
        return api_response(
            status=ResponseStatus.ERROR,
            message="Đã xảy ra lỗi không mong muốn từ Server hãy thử lại sau",
            data=str(e),
            status_code=ResponseStatusCode.INTERNAL_SERVER_ERROR
        )


async def delete_ticket(ticket_id: UUID, db: AsyncSession, current_user: User):
    """Xóa ticket (hard delete vì có cascade)"""
    try:
        is_super_admin = await isCheckMaxLevel(current_user, db)
        
        # Get ticket
        query = select(Ticket).where(Ticket.id == ticket_id)
        if not is_super_admin:
            query = query.where(Ticket.tenant_id == current_user.tenant_id)
        
        result = await db.execute(query)
        ticket = result.scalar_one_or_none()
        
        if not ticket:
            return api_response(
                status=ResponseStatus.ERROR,
                message="Ticket không tồn tại",
                data=None,
                status_code=ResponseStatusCode.NOT_FOUND
            )
        
        # Delete ticket (cascade will handle related records)
        await db.delete(ticket)
        await db.commit()
        
        return api_response(
            status=ResponseStatus.SUCCESS,
            message="Xóa ticket thành công",
            data=None,
            status_code=ResponseStatusCode.OK
        )
    except SQLAlchemyError as e:
        await db.rollback()
        logger.error(f"Database error in delete_ticket: {str(e)}")
        return api_response(
            status=ResponseStatus.ERROR,
            message="Lỗi truy vấn cơ sở dữ liệu",
            data=str(e),
            status_code=ResponseStatusCode.INTERNAL_SERVER_ERROR
        )
    except Exception as e:
        await db.rollback()
        logger.error(f"Error in delete_ticket: {str(e)}")
        return api_response(
            status=ResponseStatus.ERROR,
            message="Đã xảy ra lỗi không mong muốn từ Server hãy thử lại sau",
            data=str(e),
            status_code=ResponseStatusCode.INTERNAL_SERVER_ERROR
        )


async def assign_ticket(ticket_id: UUID, assign_data: TicketAssign, db: AsyncSession, current_user: User):
    """Gán ticket cho user"""
    try:
        is_super_admin = await isCheckMaxLevel(current_user, db)
        actor_type = await get_user_role_name(current_user, db)
        
        # Get ticket
        query = select(Ticket).where(Ticket.id == ticket_id)
        if not is_super_admin:
            query = query.where(Ticket.tenant_id == current_user.tenant_id)
        
        result = await db.execute(query)
        ticket = result.scalar_one_or_none()
        
        if not ticket:
            return api_response(
                status=ResponseStatus.ERROR,
                message="Ticket không tồn tại",
                data=None,
                status_code=ResponseStatusCode.NOT_FOUND
            )
        
        # Validate assigned user
        user_query = select(User).where(
            and_(
                User.id == assign_data.assigned_to,
                User.tenant_id == ticket.tenant_id,
                User.is_active == 1
            )
        )
        user_result = await db.execute(user_query)
        assigned_user = user_result.scalar_one_or_none()
        
        if not assigned_user:
            return api_response(
                status=ResponseStatus.ERROR,
                message="User được gán không tồn tại hoặc không hoạt động",
                data=None,
                status_code=ResponseStatusCode.BAD_REQUEST
            )
        
        old_assigned_to = ticket.assigned_to
        ticket.assigned_to = assign_data.assigned_to
        
        # Log event
        await log_ticket_event(
            db=db,
            ticket_id=ticket_id,
            event_type="ASSIGNED",
            actor_id=str(current_user.id),
            actor_type=actor_type,
            payload={
                "old_assigned_to": str(old_assigned_to) if old_assigned_to else None,
                "new_assigned_to": str(assign_data.assigned_to),
                "assigned_to_name": assigned_user.fullname or assigned_user.username
            },
            tenant_id=ticket.tenant_id
        )
        
        await db.commit()
        
        return api_response(
            status=ResponseStatus.SUCCESS,
            message="Gán ticket thành công",
            data={"id": str(ticket.id), "code": ticket.code, "assigned_to": str(ticket.assigned_to)},
            status_code=ResponseStatusCode.OK
        )
    except SQLAlchemyError as e:
        await db.rollback()
        logger.error(f"Database error in assign_ticket: {str(e)}")
        return api_response(
            status=ResponseStatus.ERROR,
            message="Lỗi truy vấn cơ sở dữ liệu",
            data=str(e),
            status_code=ResponseStatusCode.INTERNAL_SERVER_ERROR
        )
    except Exception as e:
        await db.rollback()
        logger.error(f"Error in assign_ticket: {str(e)}")
        return api_response(
            status=ResponseStatus.ERROR,
            message="Đã xảy ra lỗi không mong muốn từ Server hãy thử lại sau",
            data=str(e),
            status_code=ResponseStatusCode.INTERNAL_SERVER_ERROR
        )


async def update_ticket_status(ticket_id: UUID, status_data: TicketStatusUpdate, db: AsyncSession, current_user: User):
    """Cập nhật trạng thái ticket"""
    try:
        is_super_admin = await isCheckMaxLevel(current_user, db)
        actor_type = await get_user_role_name(current_user, db)
        
        # Get ticket
        query = select(Ticket).where(Ticket.id == ticket_id)
        if not is_super_admin:
            query = query.where(Ticket.tenant_id == current_user.tenant_id)
        
        result = await db.execute(query)
        ticket = result.scalar_one_or_none()
        
        if not ticket:
            return api_response(
                status=ResponseStatus.ERROR,
                message="Ticket không tồn tại",
                data=None,
                status_code=ResponseStatusCode.NOT_FOUND
            )
        
        old_status = ticket.status
        ticket.status = status_data.status
        
        # Set closed_at if status is closed
        if status_data.status == TicketStatus.CLOSED:
            ticket.closed_at = datetime.now(timezone.utc)
            event_type = "CLOSED"
        elif old_status == TicketStatus.CLOSED and status_data.status != TicketStatus.CLOSED:
            ticket.closed_at = None
            event_type = "REOPENED"
        else:
            event_type = "STATUS_CHANGED"
        
        # Log event
        await log_ticket_event(
            db=db,
            ticket_id=ticket_id,
            event_type=event_type,
            actor_id=str(current_user.id),
            actor_type=actor_type,
            payload={
                "old_status": old_status.value,
                "new_status": status_data.status.value,
                "note": status_data.note
            },
            tenant_id=ticket.tenant_id
        )
        
        await db.commit()
        
        return api_response(
            status=ResponseStatus.SUCCESS,
            message="Cập nhật trạng thái ticket thành công",
            data={"id": str(ticket.id), "code": ticket.code, "status": ticket.status.value},
            status_code=ResponseStatusCode.OK
        )
    except SQLAlchemyError as e:
        await db.rollback()
        logger.error(f"Database error in update_ticket_status: {str(e)}")
        return api_response(
            status=ResponseStatus.ERROR,
            message="Lỗi truy vấn cơ sở dữ liệu",
            data=str(e),
            status_code=ResponseStatusCode.INTERNAL_SERVER_ERROR
        )
    except Exception as e:
        await db.rollback()
        logger.error(f"Error in update_ticket_status: {str(e)}")
        return api_response(
            status=ResponseStatus.ERROR,
            message="Đã xảy ra lỗi không mong muốn từ Server hãy thử lại sau",
            data=str(e),
            status_code=ResponseStatusCode.INTERNAL_SERVER_ERROR
        )
