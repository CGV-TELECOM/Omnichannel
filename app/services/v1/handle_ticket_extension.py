from sqlalchemy.ext.asyncio import AsyncSession
from app.schemas.responses.api_response_rule import api_response, ResponseStatus, ResponseStatusCode
from app.db.models import TicketExtension, User, Ticket
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from app.schemas.requests.ticket_extension import (
    TicketExtensionCreate,
    TicketExtensionUpdate,
    TicketExtensionResponse
)
from app.utils.helpers import is_platform_admin
from uuid import UUID
from typing import Optional, Dict, Any
import json
import logging

logger = logging.getLogger(__name__)

async def get_ticket_extension(ticket_id: UUID, db: AsyncSession, current_user: User):
    """
    Lấy extension data của một ticket
    """
    try:
        is_super_admin = await is_platform_admin(current_user, db)
        
        # Validate ticket exists and user has access
        ticket_query = select(Ticket).where(Ticket.id == ticket_id)
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
        
        # Get extension
        extension_query = select(TicketExtension).where(TicketExtension.ticket_id == ticket_id)
        result = await db.execute(extension_query)
        extension = result.scalar_one_or_none()
        
        if not extension:
            return api_response(
                status=ResponseStatus.SUCCESS,
                status_code=ResponseStatusCode.OK,
                message="Ticket chưa có extension data",
                data={
                    "ticket_id": ticket_id,
                    "data": None
                }
            )
        
        extension_data = {
            "ticket_id": extension.ticket_id,
            "data": json.loads(extension.data.decode('utf-8')) if extension.data else None
        }
        
        return api_response(
            status=ResponseStatus.SUCCESS,
            status_code=ResponseStatusCode.OK,
            message="Lấy extension data thành công",
            data=extension_data
        )
    except SQLAlchemyError as e:
        logger.error(f"Database error in get_ticket_extension: {str(e)}")
        return api_response(
            status=ResponseStatus.ERROR,
            status_code=ResponseStatusCode.INTERNAL_SERVER_ERROR,
            message="Lỗi truy vấn cơ sở dữ liệu",
            data=str(e)
        )
    except Exception as e:
        logger.error(f"Unexpected error in get_ticket_extension: {str(e)}")
        return api_response(
            status=ResponseStatus.ERROR,
            status_code=ResponseStatusCode.INTERNAL_SERVER_ERROR,
            message="Đã xảy ra lỗi không mong muốn",
            data=str(e)
        )


async def upsert_ticket_extension(extension_data: TicketExtensionCreate, db: AsyncSession, current_user: User):
    """
    Tạo hoặc cập nhật extension data cho ticket (Upsert)
    """
    try:
        is_super_admin = await is_platform_admin(current_user, db)
        
        # Validate ticket exists and user has access
        ticket_query = select(Ticket).where(Ticket.id == extension_data.ticket_id)
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
        
        # Convert data dict to binary JSON
        data_binary = None
        if extension_data.data:
            data_binary = json.dumps(extension_data.data).encode('utf-8')
        
        # Check if extension already exists
        extension_query = select(TicketExtension).where(TicketExtension.ticket_id == extension_data.ticket_id)
        result = await db.execute(extension_query)
        existing_extension = result.scalar_one_or_none()
        
        if existing_extension:
            # Update existing extension
            existing_extension.data = data_binary
            await db.commit()
            await db.refresh(existing_extension)
            
            extension_response = {
                "ticket_id": existing_extension.ticket_id,
                "data": json.loads(existing_extension.data.decode('utf-8')) if existing_extension.data else None
            }
            
            return api_response(
                status=ResponseStatus.SUCCESS,
                status_code=ResponseStatusCode.OK,
                message="Cập nhật extension data thành công",
                data=extension_response
            )
        else:
            # Create new extension
            new_extension = TicketExtension(
                ticket_id=extension_data.ticket_id,
                data=data_binary
            )
            
            db.add(new_extension)
            await db.commit()
            await db.refresh(new_extension)
            
            extension_response = {
                "ticket_id": new_extension.ticket_id,
                "data": json.loads(new_extension.data.decode('utf-8')) if new_extension.data else None
            }
            
            return api_response(
                status=ResponseStatus.SUCCESS,
                status_code=ResponseStatusCode.CREATED,
                message="Tạo extension data thành công",
                data=extension_response
            )
    except SQLAlchemyError as e:
        await db.rollback()
        logger.error(f"Database error in upsert_ticket_extension: {str(e)}")
        return api_response(
            status=ResponseStatus.ERROR,
            status_code=ResponseStatusCode.INTERNAL_SERVER_ERROR,
            message="Lỗi khi lưu extension data",
            data=str(e)
        )
    except Exception as e:
        await db.rollback()
        logger.error(f"Unexpected error in upsert_ticket_extension: {str(e)}")
        return api_response(
            status=ResponseStatus.ERROR,
            status_code=ResponseStatusCode.INTERNAL_SERVER_ERROR,
            message="Đã xảy ra lỗi không mong muốn",
            data=str(e)
        )


async def update_ticket_extension(ticket_id: UUID, extension_data: TicketExtensionUpdate, db: AsyncSession, current_user: User):
    """
    Cập nhật extension data cho ticket (Merge với data hiện tại)
    """
    try:
        is_super_admin = await is_platform_admin(current_user, db)
        
        # Validate ticket exists and user has access
        ticket_query = select(Ticket).where(Ticket.id == ticket_id)
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
        
        # Get existing extension
        extension_query = select(TicketExtension).where(TicketExtension.ticket_id == ticket_id)
        result = await db.execute(extension_query)
        extension = result.scalar_one_or_none()
        
        if not extension:
            return api_response(
                status=ResponseStatus.ERROR,
                status_code=ResponseStatusCode.NOT_FOUND,
                message="Ticket chưa có extension data. Sử dụng POST để tạo mới."
            )
        
        # Merge new data with existing data
        existing_data = json.loads(extension.data.decode('utf-8')) if extension.data else {}
        
        if extension_data.data:
            existing_data.update(extension_data.data)
        
        # Save merged data
        extension.data = json.dumps(existing_data).encode('utf-8')
        await db.commit()
        await db.refresh(extension)
        
        extension_response = {
            "ticket_id": extension.ticket_id,
            "data": json.loads(extension.data.decode('utf-8')) if extension.data else None
        }
        
        return api_response(
            status=ResponseStatus.SUCCESS,
            status_code=ResponseStatusCode.OK,
            message="Cập nhật extension data thành công",
            data=extension_response
        )
    except SQLAlchemyError as e:
        await db.rollback()
        logger.error(f"Database error in update_ticket_extension: {str(e)}")
        return api_response(
            status=ResponseStatus.ERROR,
            status_code=ResponseStatusCode.INTERNAL_SERVER_ERROR,
            message="Lỗi khi cập nhật extension data",
            data=str(e)
        )
    except Exception as e:
        await db.rollback()
        logger.error(f"Unexpected error in update_ticket_extension: {str(e)}")
        return api_response(
            status=ResponseStatus.ERROR,
            status_code=ResponseStatusCode.INTERNAL_SERVER_ERROR,
            message="Đã xảy ra lỗi không mong muốn",
            data=str(e)
        )


async def delete_ticket_extension(ticket_id: UUID, db: AsyncSession, current_user: User):
    """
    Xóa extension data của ticket
    """
    try:
        is_super_admin = await is_platform_admin(current_user, db)
        
        # Validate ticket exists and user has access
        ticket_query = select(Ticket).where(Ticket.id == ticket_id)
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
        
        # Get extension
        extension_query = select(TicketExtension).where(TicketExtension.ticket_id == ticket_id)
        result = await db.execute(extension_query)
        extension = result.scalar_one_or_none()
        
        if not extension:
            return api_response(
                status=ResponseStatus.ERROR,
                status_code=ResponseStatusCode.NOT_FOUND,
                message="Ticket không có extension data"
            )
        
        await db.delete(extension)
        await db.commit()
        
        return api_response(
            status=ResponseStatus.SUCCESS,
            status_code=ResponseStatusCode.OK,
            message="Xóa extension data thành công"
        )
    except SQLAlchemyError as e:
        await db.rollback()
        logger.error(f"Database error in delete_ticket_extension: {str(e)}")
        return api_response(
            status=ResponseStatus.ERROR,
            status_code=ResponseStatusCode.INTERNAL_SERVER_ERROR,
            message="Lỗi khi xóa extension data",
            data=str(e)
        )
    except Exception as e:
        await db.rollback()
        logger.error(f"Unexpected error in delete_ticket_extension: {str(e)}")
        return api_response(
            status=ResponseStatus.ERROR,
            status_code=ResponseStatusCode.INTERNAL_SERVER_ERROR,
            message="Đã xảy ra lỗi không mong muốn",
            data=str(e)
        )
