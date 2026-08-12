from sqlalchemy.ext.asyncio import AsyncSession
from app.schemas.responses.api_response_rule import api_response, ResponseStatus, ResponseStatusCode
from app.db.models import TicketTemplate, User, Tenant
from sqlalchemy import select, func, or_, and_
from sqlalchemy.exc import SQLAlchemyError
from app.schemas.requests.ticket_template import (
    TicketTemplateCreate,
    TicketTemplateUpdate,
    TicketTemplateResponse
)
from app.utils.helpers import is_platform_admin
from uuid import UUID
from typing import Optional, Dict, Any
from datetime import datetime, timezone
import json
import logging

logger = logging.getLogger(__name__)

async def get_ticket_templates(
    db: AsyncSession,
    current_user: User,
    id: Optional[UUID] = None,
    name: Optional[str] = None,
    is_active: Optional[bool] = None,
    page: int = 1,
    page_size: int = 10,
    search: Optional[str] = None,
    sort_by: Optional[str] = None,
    sort_order: str = "desc",
    tenant_id: Optional[UUID] = None
):
    """
    Lấy danh sách ticket templates với pagination, search và filtering
    """
    try:
        # Check permissions
        is_super_admin = await is_platform_admin(current_user, db)
        
        # Build base query
        query = select(TicketTemplate)
        
        # Build filters
        filters = []
        
        # Filter by tenant_id
        if is_super_admin and tenant_id:
            filters.append(TicketTemplate.tenant_id == tenant_id)
        elif not is_super_admin:
            filters.append(TicketTemplate.tenant_id == current_user.tenant_id)
        
        # Filter by ID
        if id:
            filters.append(TicketTemplate.id == id)
        
        # Filter by name
        if name:
            filters.append(TicketTemplate.name.ilike(f"%{name}%"))
        
        # Filter by is_active
        if is_active is not None:
            filters.append(TicketTemplate.is_active == is_active)
        
        # Search across multiple fields
        if search:
            search_filter = or_(
                TicketTemplate.name.ilike(f"%{search}%"),
                TicketTemplate.description.ilike(f"%{search}%")
            )
            filters.append(search_filter)
        
        # Apply filters to query
        if filters:
            query = query.where(and_(*filters))
        
        # Count total records
        count_query = select(func.count()).select_from(TicketTemplate)
        if filters:
            count_query = count_query.where(and_(*filters))
        
        total_count_result = await db.execute(count_query)
        total_count = total_count_result.scalar()
        
        # Sorting
        if sort_by:
            column = getattr(TicketTemplate, sort_by, None)
            if column:
                if sort_order.lower() == "desc":
                    query = query.order_by(column.desc())
                else:
                    query = query.order_by(column.asc())
        else:
            # Default sort by created_at desc
            query = query.order_by(TicketTemplate.created_at.desc())
        
        # Pagination
        offset = (page - 1) * page_size
        query = query.offset(offset).limit(page_size)
        
        # Execute query
        result = await db.execute(query)
        templates = result.scalars().all()
        
        # Convert to response format
        template_list = []
        for template in templates:
            template_dict = {
                "id": template.id,
                "tenant_id": template.tenant_id,
                "name": template.name,
                "description": template.description,
                "flow_id": template.flow_id,
                "sla_id": template.sla_id,
                "extension_schema": json.loads(template.extension_schema.decode('utf-8')) if template.extension_schema else None,
                "is_active": template.is_active,
                "created_at": template.created_at,
                "updated_at": template.updated_at
            }
            template_list.append(template_dict)
        
        total_pages = (total_count + page_size - 1) // page_size
        
        return api_response(
            status=ResponseStatus.SUCCESS,
            status_code=ResponseStatusCode.OK,
            message="Lấy danh sách ticket templates thành công",
            data={
                "templates": template_list,
                "pagination": {
                    "current_page": page,
                    "page_size": page_size,
                    "total_count": total_count,
                    "total_pages": total_pages
                }
            }
        )
    except SQLAlchemyError as e:
        logger.error(f"Database error in get_ticket_templates: {str(e)}")
        return api_response(
            status=ResponseStatus.ERROR,
            status_code=ResponseStatusCode.INTERNAL_SERVER_ERROR,
            message="Lỗi truy vấn cơ sở dữ liệu",
            data=str(e)
        )
    except Exception as e:
        logger.error(f"Unexpected error in get_ticket_templates: {str(e)}")
        return api_response(
            status=ResponseStatus.ERROR,
            status_code=ResponseStatusCode.INTERNAL_SERVER_ERROR,
            message="Đã xảy ra lỗi không mong muốn",
            data=str(e)
        )


async def get_ticket_template_by_id(template_id: UUID, db: AsyncSession, current_user: User):
    """
    Lấy thông tin chi tiết một ticket template theo ID
    """
    try:
        is_super_admin = await is_platform_admin(current_user, db)
        
        query = select(TicketTemplate).where(TicketTemplate.id == template_id)
        
        if not is_super_admin:
            query = query.where(TicketTemplate.tenant_id == current_user.tenant_id)
        
        result = await db.execute(query)
        template = result.scalar_one_or_none()
        
        if not template:
            return api_response(
                status=ResponseStatus.ERROR,
                status_code=ResponseStatusCode.NOT_FOUND,
                message="Không tìm thấy ticket template"
            )
        
        template_data = {
            "id": template.id,
            "tenant_id": template.tenant_id,
            "name": template.name,
            "description": template.description,
            "flow_id": template.flow_id,
            "sla_id": template.sla_id,
            "extension_schema": json.loads(template.extension_schema.decode('utf-8')) if template.extension_schema else None,
            "is_active": template.is_active,
            "created_at": template.created_at,
            "updated_at": template.updated_at
        }
        
        return api_response(
            status=ResponseStatus.SUCCESS,
            status_code=ResponseStatusCode.OK,
            message="Lấy thông tin ticket template thành công",
            data=template_data
        )
    except SQLAlchemyError as e:
        logger.error(f"Database error in get_ticket_template_by_id: {str(e)}")
        return api_response(
            status=ResponseStatus.ERROR,
            status_code=ResponseStatusCode.INTERNAL_SERVER_ERROR,
            message="Lỗi truy vấn cơ sở dữ liệu",
            data=str(e)
        )
    except Exception as e:
        logger.error(f"Unexpected error in get_ticket_template_by_id: {str(e)}")
        return api_response(
            status=ResponseStatus.ERROR,
            status_code=ResponseStatusCode.INTERNAL_SERVER_ERROR,
            message="Đã xảy ra lỗi không mong muốn",
            data=str(e)
        )


async def create_ticket_template(template_data: TicketTemplateCreate, db: AsyncSession, current_user: User):
    """
    Tạo ticket template mới
    """
    try:
        is_super_admin = await is_platform_admin(current_user, db)
        
        # Set tenant_id
        if is_super_admin and template_data.tenant_id:
            # Validate tenant exists
            tenant_check = await db.execute(select(Tenant).where(Tenant.id == template_data.tenant_id))
            if not tenant_check.scalar_one_or_none():
                return api_response(
                    status=ResponseStatus.ERROR,
                    status_code=ResponseStatusCode.NOT_FOUND,
                    message="Tenant không tồn tại"
                )
            tenant_id = template_data.tenant_id
        else:
            tenant_id = current_user.tenant_id
        
        # Convert extension_schema dict to binary JSON
        extension_schema_binary = None
        if template_data.extension_schema:
            extension_schema_binary = json.dumps(template_data.extension_schema).encode('utf-8')
        
        # Create new ticket template
        new_template = TicketTemplate(
            tenant_id=tenant_id,
            name=template_data.name,
            description=template_data.description,
            flow_id=template_data.flow_id,
            sla_id=template_data.sla_id,
            extension_schema=extension_schema_binary,
            is_active=template_data.is_active if template_data.is_active is not None else True
        )
        
        db.add(new_template)
        await db.commit()
        await db.refresh(new_template)
        
        template_response = {
            "id": new_template.id,
            "tenant_id": new_template.tenant_id,
            "name": new_template.name,
            "description": new_template.description,
            "flow_id": new_template.flow_id,
            "sla_id": new_template.sla_id,
            "extension_schema": json.loads(new_template.extension_schema.decode('utf-8')) if new_template.extension_schema else None,
            "is_active": new_template.is_active,
            "created_at": new_template.created_at,
            "updated_at": new_template.updated_at
        }
        
        return api_response(
            status=ResponseStatus.SUCCESS,
            status_code=ResponseStatusCode.CREATED,
            message="Tạo ticket template thành công",
            data=template_response
        )
    except SQLAlchemyError as e:
        await db.rollback()
        logger.error(f"Database error in create_ticket_template: {str(e)}")
        return api_response(
            status=ResponseStatus.ERROR,
            status_code=ResponseStatusCode.INTERNAL_SERVER_ERROR,
            message="Lỗi khi tạo ticket template",
            data=str(e)
        )
    except Exception as e:
        await db.rollback()
        logger.error(f"Unexpected error in create_ticket_template: {str(e)}")
        return api_response(
            status=ResponseStatus.ERROR,
            status_code=ResponseStatusCode.INTERNAL_SERVER_ERROR,
            message="Đã xảy ra lỗi không mong muốn",
            data=str(e)
        )


async def update_ticket_template(template_id: UUID, template_data: TicketTemplateUpdate, db: AsyncSession, current_user: User):
    """
    Cập nhật ticket template
    """
    try:
        is_super_admin = await is_platform_admin(current_user, db)
        
        # Check if template exists and user has permission
        query = select(TicketTemplate).where(TicketTemplate.id == template_id)
        if not is_super_admin:
            query = query.where(TicketTemplate.tenant_id == current_user.tenant_id)
        
        result = await db.execute(query)
        template = result.scalar_one_or_none()
        
        if not template:
            return api_response(
                status=ResponseStatus.ERROR,
                status_code=ResponseStatusCode.NOT_FOUND,
                message="Không tìm thấy ticket template hoặc bạn không có quyền truy cập"
            )
        
        # Update fields
        update_data = template_data.model_dump(exclude_unset=True)
        
        # Convert extension_schema if provided
        if 'extension_schema' in update_data and update_data['extension_schema'] is not None:
            update_data['extension_schema'] = json.dumps(update_data['extension_schema']).encode('utf-8')
        
        for key, value in update_data.items():
            setattr(template, key, value)
        
        # Update updated_at timestamp
        template.updated_at = datetime.now(timezone.utc)
        
        await db.commit()
        await db.refresh(template)
        
        template_response = {
            "id": template.id,
            "tenant_id": template.tenant_id,
            "name": template.name,
            "description": template.description,
            "flow_id": template.flow_id,
            "sla_id": template.sla_id,
            "extension_schema": json.loads(template.extension_schema.decode('utf-8')) if template.extension_schema else None,
            "is_active": template.is_active,
            "created_at": template.created_at,
            "updated_at": template.updated_at
        }
        
        return api_response(
            status=ResponseStatus.SUCCESS,
            status_code=ResponseStatusCode.OK,
            message="Cập nhật ticket template thành công",
            data=template_response
        )
    except SQLAlchemyError as e:
        await db.rollback()
        logger.error(f"Database error in update_ticket_template: {str(e)}")
        return api_response(
            status=ResponseStatus.ERROR,
            status_code=ResponseStatusCode.INTERNAL_SERVER_ERROR,
            message="Lỗi khi cập nhật ticket template",
            data=str(e)
        )
    except Exception as e:
        await db.rollback()
        logger.error(f"Unexpected error in update_ticket_template: {str(e)}")
        return api_response(
            status=ResponseStatus.ERROR,
            status_code=ResponseStatusCode.INTERNAL_SERVER_ERROR,
            message="Đã xảy ra lỗi không mong muốn",
            data=str(e)
        )


async def delete_ticket_template(template_id: UUID, db: AsyncSession, current_user: User):
    """
    Xóa ticket template (soft delete bằng cách set is_active = False)
    """
    try:
        is_super_admin = await is_platform_admin(current_user, db)
        
        # Check if template exists and user has permission
        query = select(TicketTemplate).where(TicketTemplate.id == template_id)
        if not is_super_admin:
            query = query.where(TicketTemplate.tenant_id == current_user.tenant_id)
        
        result = await db.execute(query)
        template = result.scalar_one_or_none()
        
        if not template:
            return api_response(
                status=ResponseStatus.ERROR,
                status_code=ResponseStatusCode.NOT_FOUND,
                message="Không tìm thấy ticket template hoặc bạn không có quyền truy cập"
            )
        
        # Soft delete
        template.is_active = False
        template.updated_at = datetime.now(timezone.utc)
        await db.commit()
        
        return api_response(
            status=ResponseStatus.SUCCESS,
            status_code=ResponseStatusCode.OK,
            message="Xóa ticket template thành công"
        )
    except SQLAlchemyError as e:
        await db.rollback()
        logger.error(f"Database error in delete_ticket_template: {str(e)}")
        return api_response(
            status=ResponseStatus.ERROR,
            status_code=ResponseStatusCode.INTERNAL_SERVER_ERROR,
            message="Lỗi khi xóa ticket template",
            data=str(e)
        )
    except Exception as e:
        await db.rollback()
        logger.error(f"Unexpected error in delete_ticket_template: {str(e)}")
        return api_response(
            status=ResponseStatus.ERROR,
            status_code=ResponseStatusCode.INTERNAL_SERVER_ERROR,
            message="Đã xảy ra lỗi không mong muốn",
            data=str(e)
        )
