from sqlalchemy.ext.asyncio import AsyncSession
from app.schemas.responses.api_response_rule import api_response, ResponseStatus, ResponseStatusCode
from app.db.models import TicketContext, User, Tenant, Ticket
from sqlalchemy import select, func, or_, and_
from sqlalchemy.exc import SQLAlchemyError
from app.schemas.requests.ticket_context import (
    TicketContextCreate,
    TicketContextUpdate,
    TicketContextResponse
)
from app.utils.helpers import isCheckMaxLevel
from uuid import UUID
from typing import Optional, Dict, Any
from datetime import datetime
import json
import logging

logger = logging.getLogger(__name__)

async def get_ticket_contexts(
    db: AsyncSession,
    current_user: User,
    id: Optional[UUID] = None,
    ticket_id: Optional[UUID] = None,
    context_type: Optional[str] = None,
    context_id: Optional[str] = None,
    source_type: Optional[str] = None,
    page: int = 1,
    page_size: int = 10,
    sort_by: Optional[str] = None,
    sort_order: str = "desc",
    tenant_id: Optional[UUID] = None
):
    """
    Lấy danh sách ticket contexts với pagination và filtering
    """
    try:
        # Check permissions
        is_super_admin = await isCheckMaxLevel(current_user, db)
        
        # Build base query
        query = select(TicketContext)
        
        # Build filters
        filters = []
        
        # Filter by tenant_id
        if is_super_admin and tenant_id:
            filters.append(TicketContext.tenant_id == tenant_id)
        elif not is_super_admin:
            filters.append(TicketContext.tenant_id == current_user.tenant_id)
        
        # Filter by ID
        if id:
            filters.append(TicketContext.id == id)
        
        # Filter by ticket_id
        if ticket_id:
            filters.append(TicketContext.ticket_id == ticket_id)
        
        # Filter by context_type
        if context_type:
            filters.append(TicketContext.context_type.ilike(f"%{context_type}%"))
        
        # Filter by context_id
        if context_id:
            filters.append(TicketContext.context_id.ilike(f"%{context_id}%"))
        
        # Filter by source_type
        if source_type:
            filters.append(TicketContext.source_type.ilike(f"%{source_type}%"))
        
        # Apply filters to query
        if filters:
            query = query.where(and_(*filters))
        
        # Count total records
        count_query = select(func.count()).select_from(TicketContext)
        if filters:
            count_query = count_query.where(and_(*filters))
        
        total_count_result = await db.execute(count_query)
        total_count = total_count_result.scalar()
        
        # Sorting
        if sort_by:
            column = getattr(TicketContext, sort_by, None)
            if column:
                if sort_order.lower() == "desc":
                    query = query.order_by(column.desc())
                else:
                    query = query.order_by(column.asc())
        else:
            # Default sort by created_at desc
            query = query.order_by(TicketContext.created_at.desc())
        
        # Pagination
        offset = (page - 1) * page_size
        query = query.offset(offset).limit(page_size)
        
        # Execute query
        result = await db.execute(query)
        contexts = result.scalars().all()
        
        # Convert to response format
        context_list = []
        for context in contexts:
            context_dict = {
                "id": context.id,
                "ticket_id": context.ticket_id,
                "context_type": context.context_type,
                "context_id": context.context_id,
                "source_type": context.source_type,
                "context_metadata": json.loads(context.context_metadata.decode('utf-8')) if context.context_metadata else None,
                "created_at": context.created_at,
                "tenant_id": context.tenant_id
            }
            context_list.append(context_dict)
        
        total_pages = (total_count + page_size - 1) // page_size
        
        return api_response(
            status=ResponseStatus.SUCCESS,
            status_code=ResponseStatusCode.OK,
            message="Lấy danh sách ticket contexts thành công",
            data={
                "contexts": context_list,
                "pagination": {
                    "current_page": page,
                    "page_size": page_size,
                    "total_count": total_count,
                    "total_pages": total_pages
                }
            }
        )
    except SQLAlchemyError as e:
        logger.error(f"Database error in get_ticket_contexts: {str(e)}")
        return api_response(
            status=ResponseStatus.ERROR,
            status_code=ResponseStatusCode.INTERNAL_SERVER_ERROR,
            message="Lỗi truy vấn cơ sở dữ liệu",
            data=str(e)
        )
    except Exception as e:
        logger.error(f"Unexpected error in get_ticket_contexts: {str(e)}")
        return api_response(
            status=ResponseStatus.ERROR,
            status_code=ResponseStatusCode.INTERNAL_SERVER_ERROR,
            message="Đã xảy ra lỗi không mong muốn",
            data=str(e)
        )


async def get_ticket_context_by_id(context_id: UUID, db: AsyncSession, current_user: User):
    """
    Lấy thông tin chi tiết một ticket context theo ID
    """
    try:
        is_super_admin = await isCheckMaxLevel(current_user, db)
        
        query = select(TicketContext).where(TicketContext.id == context_id)
        
        if not is_super_admin:
            query = query.where(TicketContext.tenant_id == current_user.tenant_id)
        
        result = await db.execute(query)
        context = result.scalar_one_or_none()
        
        if not context:
            return api_response(
                status=ResponseStatus.ERROR,
                status_code=ResponseStatusCode.NOT_FOUND,
                message="Không tìm thấy ticket context"
            )
        
        context_data = {
            "id": context.id,
            "ticket_id": context.ticket_id,
            "context_type": context.context_type,
            "context_id": context.context_id,
            "source_type": context.source_type,
            "context_metadata": json.loads(context.context_metadata.decode('utf-8')) if context.context_metadata else None,
            "created_at": context.created_at,
            "tenant_id": context.tenant_id
        }
        
        return api_response(
            status=ResponseStatus.SUCCESS,
            status_code=ResponseStatusCode.OK,
            message="Lấy thông tin ticket context thành công",
            data=context_data
        )
    except SQLAlchemyError as e:
        logger.error(f"Database error in get_ticket_context_by_id: {str(e)}")
        return api_response(
            status=ResponseStatus.ERROR,
            status_code=ResponseStatusCode.INTERNAL_SERVER_ERROR,
            message="Lỗi truy vấn cơ sở dữ liệu",
            data=str(e)
        )
    except Exception as e:
        logger.error(f"Unexpected error in get_ticket_context_by_id: {str(e)}")
        return api_response(
            status=ResponseStatus.ERROR,
            status_code=ResponseStatusCode.INTERNAL_SERVER_ERROR,
            message="Đã xảy ra lỗi không mong muốn",
            data=str(e)
        )


async def create_ticket_context(context_data: TicketContextCreate, db: AsyncSession, current_user: User):
    """
    Tạo ticket context mới
    """
    try:
        is_super_admin = await isCheckMaxLevel(current_user, db)
        
        # Validate ticket exists and user has access
        ticket_query = select(Ticket).where(Ticket.id == context_data.ticket_id)
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
        if is_super_admin and context_data.tenant_id:
            # Validate tenant exists
            tenant_check = await db.execute(select(Tenant).where(Tenant.id == context_data.tenant_id))
            if not tenant_check.scalar_one_or_none():
                return api_response(
                    status=ResponseStatus.ERROR,
                    status_code=ResponseStatusCode.NOT_FOUND,
                    message="Tenant không tồn tại"
                )
            tenant_id = context_data.tenant_id
        else:
            tenant_id = current_user.tenant_id
        
        # Convert context_metadata dict to binary JSON
        context_metadata_binary = None
        if context_data.context_metadata:
            context_metadata_binary = json.dumps(context_data.context_metadata).encode('utf-8')
        
        # Create new ticket context
        new_context = TicketContext(
            ticket_id=context_data.ticket_id,
            context_type=context_data.context_type,
            context_id=context_data.context_id,
            source_type=context_data.source_type,
            context_metadata=context_metadata_binary,
            tenant_id=tenant_id
        )
        
        db.add(new_context)
        await db.commit()
        await db.refresh(new_context)
        
        context_response = {
            "id": new_context.id,
            "ticket_id": new_context.ticket_id,
            "context_type": new_context.context_type,
            "context_id": new_context.context_id,
            "source_type": new_context.source_type,
            "context_metadata": json.loads(new_context.context_metadata.decode('utf-8')) if new_context.context_metadata else None,
            "created_at": new_context.created_at,
            "tenant_id": new_context.tenant_id
        }
        
        return api_response(
            status=ResponseStatus.SUCCESS,
            status_code=ResponseStatusCode.CREATED,
            message="Tạo ticket context thành công",
            data=context_response
        )
    except SQLAlchemyError as e:
        await db.rollback()
        logger.error(f"Database error in create_ticket_context: {str(e)}")
        return api_response(
            status=ResponseStatus.ERROR,
            status_code=ResponseStatusCode.INTERNAL_SERVER_ERROR,
            message="Lỗi khi tạo ticket context",
            data=str(e)
        )
    except Exception as e:
        await db.rollback()
        logger.error(f"Unexpected error in create_ticket_context: {str(e)}")
        return api_response(
            status=ResponseStatus.ERROR,
            status_code=ResponseStatusCode.INTERNAL_SERVER_ERROR,
            message="Đã xảy ra lỗi không mong muốn",
            data=str(e)
        )


async def update_ticket_context(context_id: UUID, context_data: TicketContextUpdate, db: AsyncSession, current_user: User):
    """
    Cập nhật ticket context
    """
    try:
        is_super_admin = await isCheckMaxLevel(current_user, db)
        
        # Check if context exists and user has permission
        query = select(TicketContext).where(TicketContext.id == context_id)
        if not is_super_admin:
            query = query.where(TicketContext.tenant_id == current_user.tenant_id)
        
        result = await db.execute(query)
        context = result.scalar_one_or_none()
        
        if not context:
            return api_response(
                status=ResponseStatus.ERROR,
                status_code=ResponseStatusCode.NOT_FOUND,
                message="Không tìm thấy ticket context hoặc bạn không có quyền truy cập"
            )
        
        # Update fields
        update_data = context_data.model_dump(exclude_unset=True)
        
        # Convert context_metadata if provided
        if 'context_metadata' in update_data and update_data['context_metadata'] is not None:
            update_data['context_metadata'] = json.dumps(update_data['context_metadata']).encode('utf-8')
        
        for key, value in update_data.items():
            setattr(context, key, value)
        
        await db.commit()
        await db.refresh(context)
        
        context_response = {
            "id": context.id,
            "ticket_id": context.ticket_id,
            "context_type": context.context_type,
            "context_id": context.context_id,
            "source_type": context.source_type,
            "context_metadata": json.loads(context.context_metadata.decode('utf-8')) if context.context_metadata else None,
            "created_at": context.created_at,
            "tenant_id": context.tenant_id
        }
        
        return api_response(
            status=ResponseStatus.SUCCESS,
            status_code=ResponseStatusCode.OK,
            message="Cập nhật ticket context thành công",
            data=context_response
        )
    except SQLAlchemyError as e:
        await db.rollback()
        logger.error(f"Database error in update_ticket_context: {str(e)}")
        return api_response(
            status=ResponseStatus.ERROR,
            status_code=ResponseStatusCode.INTERNAL_SERVER_ERROR,
            message="Lỗi khi cập nhật ticket context",
            data=str(e)
        )
    except Exception as e:
        await db.rollback()
        logger.error(f"Unexpected error in update_ticket_context: {str(e)}")
        return api_response(
            status=ResponseStatus.ERROR,
            status_code=ResponseStatusCode.INTERNAL_SERVER_ERROR,
            message="Đã xảy ra lỗi không mong muốn",
            data=str(e)
        )


async def delete_ticket_context(context_id: UUID, db: AsyncSession, current_user: User):
    """
    Xóa ticket context (hard delete)
    """
    try:
        is_super_admin = await isCheckMaxLevel(current_user, db)
        
        # Check if context exists and user has permission
        query = select(TicketContext).where(TicketContext.id == context_id)
        if not is_super_admin:
            query = query.where(TicketContext.tenant_id == current_user.tenant_id)
        
        result = await db.execute(query)
        context = result.scalar_one_or_none()
        
        if not context:
            return api_response(
                status=ResponseStatus.ERROR,
                status_code=ResponseStatusCode.NOT_FOUND,
                message="Không tìm thấy ticket context hoặc bạn không có quyền truy cập"
            )
        
        await db.delete(context)
        await db.commit()
        
        return api_response(
            status=ResponseStatus.SUCCESS,
            status_code=ResponseStatusCode.OK,
            message="Xóa ticket context thành công"
        )
    except SQLAlchemyError as e:
        await db.rollback()
        logger.error(f"Database error in delete_ticket_context: {str(e)}")
        return api_response(
            status=ResponseStatus.ERROR,
            status_code=ResponseStatusCode.INTERNAL_SERVER_ERROR,
            message="Lỗi khi xóa ticket context",
            data=str(e)
        )
    except Exception as e:
        await db.rollback()
        logger.error(f"Unexpected error in delete_ticket_context: {str(e)}")
        return api_response(
            status=ResponseStatus.ERROR,
            status_code=ResponseStatusCode.INTERNAL_SERVER_ERROR,
            message="Đã xảy ra lỗi không mong muốn",
            data=str(e)
        )
