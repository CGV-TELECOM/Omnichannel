from sqlalchemy.ext.asyncio import AsyncSession
from app.schemas.responses.api_response_rule import api_response, ResponseStatus, ResponseStatusCode
from app.db.models import TicketFlowInstance, Ticket, TicketFlow, TicketFlowStep, User, Tenant
from sqlalchemy import select, func, or_, and_
from sqlalchemy.exc import SQLAlchemyError, IntegrityError
from sqlalchemy.orm import selectinload
from app.schemas.requests.ticket_flow_instance import TicketFlowInstanceCreate, TicketFlowInstanceUpdate
from app.utils.helpers import is_platform_admin
from uuid import UUID
from datetime import datetime, timezone
from app.db.models import FlowInstanceStatus


async def get_ticket_flow_instances(
    db: AsyncSession,
    current_user: User,
    id: UUID | None = None,
    ticket_id: UUID | None = None,
    flow_id: UUID | None = None,
    current_step_id: UUID | None = None,
    status: str | None = None,
    page: int = 1,
    page_size: int = 10,
    sort_by: str | None = None,
    sort_order: str = "asc"
):
    """
    Lấy danh sách ticket flow instances với phân trang, tìm kiếm và filter
    """
    try:
        # Nếu có ID cụ thể, trả về instance đó
        if id:
            return await get_ticket_flow_instance_by_id(id, db, current_user)

        # Check quyền super admin (level cao nhất)
        max_level_user = await is_platform_admin(current_user, db)

        # Check tenant active
        if not max_level_user:
            tenant = await db.scalar(
                select(Tenant).where(
                    Tenant.id == current_user.tenant_id,
                    Tenant.is_active == 1
                )
            )
            if not tenant:
                return api_response(
                    status=ResponseStatus.ERROR,
                    status_code=ResponseStatusCode.FORBIDDEN,
                    message="Tenant không tồn tại hoặc đã bị vô hiệu hóa"
                )

        # Query base
        base_query = (
            select(TicketFlowInstance)
            .options(
                selectinload(TicketFlowInstance.ticket),
                selectinload(TicketFlowInstance.flow),
            )
        )

        # Filter theo tenant - user chỉ thấy instances của tenant mình
        if not max_level_user:
            base_query = base_query.where(TicketFlowInstance.tenant_id == current_user.tenant_id)

        # Filter theo ticket_id
        if ticket_id:
            base_query = base_query.where(TicketFlowInstance.ticket_id == ticket_id)

        # Filter theo flow_id
        if flow_id:
            base_query = base_query.where(TicketFlowInstance.flow_id == flow_id)

        # Filter theo current_step_id
        if current_step_id:
            base_query = base_query.where(TicketFlowInstance.current_step_id == current_step_id)

        # Filter theo status
        if status:
            try:
                status_enum = FlowInstanceStatus(status.lower())
                base_query = base_query.where(TicketFlowInstance.status == status_enum)
            except ValueError:
                return api_response(
                    status=ResponseStatus.ERROR,
                    status_code=ResponseStatusCode.BAD_REQUEST,
                    message=f"Status không hợp lệ. Các giá trị hợp lệ: {', '.join([s.value for s in FlowInstanceStatus])}"
                )

        # Sort
        if sort_by and hasattr(TicketFlowInstance, sort_by):
            sort_col = getattr(TicketFlowInstance, sort_by)
            base_query = base_query.order_by(
                sort_col.desc() if sort_order.lower() == "desc" else sort_col.asc()
            )
        else:
            # Mặc định sắp xếp theo started_at desc
            base_query = base_query.order_by(TicketFlowInstance.started_at.desc())

        # Count total
        count_query = select(func.count()).select_from(base_query.subquery())
        total_count = await db.scalar(count_query) or 0
        total_pages = (total_count + page_size - 1) // page_size

        # Pagination
        base_query = base_query.offset((page - 1) * page_size).limit(page_size)

        # Execute
        results = await db.execute(base_query)
        instances = results.scalars().all()

        # Format data
        instance_list = []
        for instance in instances:
            instance_data = {
                "id": str(instance.id),
                "ticket_id": str(instance.ticket_id),
                "flow_id": str(instance.flow_id),
                "current_step_id": str(instance.current_step_id) if instance.current_step_id else None,
                "status": instance.status.value,
                "started_at": instance.started_at,
                "finished_at": instance.finished_at,
                "tenant_id": str(instance.tenant_id) if instance.tenant_id else None
            }
            
            # Thêm thông tin ticket nếu có
            if instance.ticket:
                instance_data["ticket"] = {
                    "id": str(instance.ticket.id),
                    "code": instance.ticket.code,
                    "title": instance.ticket.title,
                    "status": instance.ticket.status.value
                }
            
            # Thêm thông tin flow nếu có
            if instance.flow:
                instance_data["flow"] = {
                    "id": str(instance.flow.id),
                    "name": instance.flow.name,
                    "description": instance.flow.description
                }
            
            instance_list.append(instance_data)

        return api_response(
            status=ResponseStatus.SUCCESS,
            status_code=ResponseStatusCode.OK,
            message="Lấy danh sách ticket flow instances thành công",
            data={
                "instances": instance_list,
                "pagination": {
                    "current_page": page,
                    "page_size": page_size,
                    "total_count": total_count,
                    "total_pages": total_pages
                }
            }
        )

    except SQLAlchemyError as e:
        return api_response(
            status=ResponseStatus.ERROR,
            status_code=ResponseStatusCode.INTERNAL_SERVER_ERROR,
            message="Lỗi truy vấn cơ sở dữ liệu",
            data=str(e)
        )
    except Exception as e:
        return api_response(
            status=ResponseStatus.ERROR,
            status_code=ResponseStatusCode.INTERNAL_SERVER_ERROR,
            message="Đã xảy ra lỗi không mong muốn",
            data=str(e)
        )


async def get_ticket_flow_instance_by_id(instance_id: UUID, db: AsyncSession, current_user: User):
    """
    Lấy thông tin chi tiết một ticket flow instance theo ID
    """
    try:
        # Check quyền super admin
        max_level_user = await is_platform_admin(current_user, db)

        # Query instance với relationships
        query = select(TicketFlowInstance).where(TicketFlowInstance.id == instance_id)
        
        # Nếu không phải super admin, chỉ xem được instance của tenant mình
        if not max_level_user:
            query = query.where(TicketFlowInstance.tenant_id == current_user.tenant_id)

        instance = await db.scalar(query)

        if not instance:
            return api_response(
                status=ResponseStatus.ERROR,
                status_code=ResponseStatusCode.NOT_FOUND,
                message="Ticket flow instance không tồn tại hoặc bạn không có quyền truy cập"
            )

        # Format data với đầy đủ thông tin
        instance_data = {
            "id": str(instance.id),
            "ticket_id": str(instance.ticket_id),
            "flow_id": str(instance.flow_id),
            "current_step_id": str(instance.current_step_id) if instance.current_step_id else None,
            "status": instance.status.value,
            "started_at": instance.started_at,
            "finished_at": instance.finished_at,
            "tenant_id": str(instance.tenant_id) if instance.tenant_id else None
        }

        # Thêm thông tin ticket
        if instance.ticket:
            instance_data["ticket"] = {
                "id": str(instance.ticket.id),
                "code": instance.ticket.code,
                "title": instance.ticket.title,
                "status": instance.ticket.status.value,
                "priority": instance.ticket.priority.value if instance.ticket.priority else None
            }

        # Thêm thông tin flow
        if instance.flow:
            instance_data["flow"] = {
                "id": str(instance.flow.id),
                "name": instance.flow.name,
                "description": instance.flow.description
            }

        # Thêm thông tin current step
        if instance.current_step_id:
            current_step = await db.scalar(
                select(TicketFlowStep).where(TicketFlowStep.id == instance.current_step_id)
            )
            if current_step:
                instance_data["current_step"] = {
                    "id": str(current_step.id),
                    "step_name": current_step.step_name,
                    "step_order": current_step.step_order,
                    "assignee_user_id": str(current_step.assignee_user_id) if current_step.assignee_user_id else None,
                    "assignee_group_id": str(current_step.assignee_group_id) if current_step.assignee_group_id else None
                }

        return api_response(
            status=ResponseStatus.SUCCESS,
            status_code=ResponseStatusCode.OK,
            message="Lấy thông tin ticket flow instance thành công",
            data=instance_data
        )

    except SQLAlchemyError as e:
        return api_response(
            status=ResponseStatus.ERROR,
            status_code=ResponseStatusCode.INTERNAL_SERVER_ERROR,
            message="Lỗi truy vấn cơ sở dữ liệu",
            data=str(e)
        )
    except Exception as e:
        return api_response(
            status=ResponseStatus.ERROR,
            status_code=ResponseStatusCode.INTERNAL_SERVER_ERROR,
            message="Đã xảy ra lỗi không mong muốn",
            data=str(e)
        )


async def create_ticket_flow_instance(instance_data: TicketFlowInstanceCreate, db: AsyncSession, current_user: User):
    """
    Tạo ticket flow instance mới
    """
    try:
        # Check quyền super admin
        max_level_user = await is_platform_admin(current_user, db)

        # Xác định tenant_id
        if max_level_user and instance_data.tenant_id:
            tenant_id = instance_data.tenant_id
        else:
            tenant_id = current_user.tenant_id

        # Check tenant active
        if tenant_id:
            tenant = await db.scalar(
                select(Tenant).where(
                    Tenant.id == tenant_id,
                    Tenant.is_active == 1
                )
            )
            if not tenant:
                return api_response(
                    status=ResponseStatus.ERROR,
                    status_code=ResponseStatusCode.FORBIDDEN,
                    message="Tenant không tồn tại hoặc đã bị vô hiệu hóa"
                )

        # Validate ticket tồn tại và thuộc tenant
        ticket = await db.scalar(
            select(Ticket).where(
                Ticket.id == instance_data.ticket_id,
                Ticket.tenant_id == tenant_id
            )
        )
        if not ticket:
            return api_response(
                status=ResponseStatus.ERROR,
                status_code=ResponseStatusCode.NOT_FOUND,
                message="Ticket không tồn tại hoặc không thuộc tenant của bạn"
            )

        # Validate flow tồn tại và thuộc tenant
        flow = await db.scalar(
            select(TicketFlow).where(
                TicketFlow.id == instance_data.flow_id,
                TicketFlow.tenant_id == tenant_id
            )
        )
        if not flow:
            return api_response(
                status=ResponseStatus.ERROR,
                status_code=ResponseStatusCode.NOT_FOUND,
                message="Flow không tồn tại hoặc không thuộc tenant của bạn"
            )

        # Validate current_step_id nếu có
        if instance_data.current_step_id:
            step = await db.scalar(
                select(TicketFlowStep).where(
                    TicketFlowStep.id == instance_data.current_step_id,
                    TicketFlowStep.flow_id == instance_data.flow_id
                )
            )
            if not step:
                return api_response(
                    status=ResponseStatus.ERROR,
                    status_code=ResponseStatusCode.BAD_REQUEST,
                    message="Step không tồn tại hoặc không thuộc flow này"
                )

        # Kiểm tra xem ticket đã có flow instance đang chạy chưa
        existing_instance = await db.scalar(
            select(TicketFlowInstance).where(
                TicketFlowInstance.ticket_id == instance_data.ticket_id,
                TicketFlowInstance.status.in_([
                    FlowInstanceStatus.PENDING,
                    FlowInstanceStatus.RUNNING,
                    FlowInstanceStatus.PAUSED
                ])
            )
        )
        if existing_instance:
            return api_response(
                status=ResponseStatus.ERROR,
                status_code=ResponseStatusCode.BAD_REQUEST,
                message=f"Ticket đã có flow instance đang chạy (ID: {existing_instance.id})"
            )

        # Tạo instance mới
        new_instance = TicketFlowInstance(
            ticket_id=instance_data.ticket_id,
            flow_id=instance_data.flow_id,
            current_step_id=instance_data.current_step_id,
            status=instance_data.status or FlowInstanceStatus.PENDING,
            tenant_id=tenant_id
        )

        db.add(new_instance)
        await db.commit()
        await db.refresh(new_instance)

        instance_response = {
            "id": str(new_instance.id),
            "ticket_id": str(new_instance.ticket_id),
            "flow_id": str(new_instance.flow_id),
            "current_step_id": str(new_instance.current_step_id) if new_instance.current_step_id else None,
            "status": new_instance.status.value,
            "started_at": new_instance.started_at,
            "finished_at": new_instance.finished_at,
            "tenant_id": str(new_instance.tenant_id) if new_instance.tenant_id else None
        }

        return api_response(
            status=ResponseStatus.SUCCESS,
            status_code=ResponseStatusCode.CREATED,
            message="Tạo ticket flow instance thành công",
            data=instance_response
        )

    except IntegrityError as e:
        await db.rollback()
        return api_response(
            status=ResponseStatus.ERROR,
            status_code=ResponseStatusCode.BAD_REQUEST,
            message="Lỗi ràng buộc dữ liệu",
            data=str(e)
        )
    except SQLAlchemyError as e:
        await db.rollback()
        return api_response(
            status=ResponseStatus.ERROR,
            status_code=ResponseStatusCode.INTERNAL_SERVER_ERROR,
            message="Lỗi truy vấn cơ sở dữ liệu",
            data=str(e)
        )
    except Exception as e:
        await db.rollback()
        return api_response(
            status=ResponseStatus.ERROR,
            status_code=ResponseStatusCode.INTERNAL_SERVER_ERROR,
            message="Đã xảy ra lỗi không mong muốn",
            data=str(e)
        )


async def update_ticket_flow_instance(instance_id: UUID, instance_data: TicketFlowInstanceUpdate, db: AsyncSession, current_user: User):
    """
    Cập nhật thông tin ticket flow instance
    """
    try:
        # Check quyền super admin
        max_level_user = await is_platform_admin(current_user, db)

        # Query instance
        query = select(TicketFlowInstance).where(TicketFlowInstance.id == instance_id)
        
        # Nếu không phải super admin, chỉ update được instance của tenant mình
        if not max_level_user:
            query = query.where(TicketFlowInstance.tenant_id == current_user.tenant_id)

        instance = await db.scalar(query)

        if not instance:
            return api_response(
                status=ResponseStatus.ERROR,
                status_code=ResponseStatusCode.NOT_FOUND,
                message="Ticket flow instance không tồn tại hoặc bạn không có quyền cập nhật"
            )

        # Validate current_step_id nếu có
        if instance_data.current_step_id:
            step = await db.scalar(
                select(TicketFlowStep).where(
                    TicketFlowStep.id == instance_data.current_step_id,
                    TicketFlowStep.flow_id == instance.flow_id
                )
            )
            if not step:
                return api_response(
                    status=ResponseStatus.ERROR,
                    status_code=ResponseStatusCode.BAD_REQUEST,
                    message="Step không tồn tại hoặc không thuộc flow này"
                )

        # Update các field
        update_data = instance_data.model_dump(exclude_unset=True)
        
        for key, value in update_data.items():
            if hasattr(instance, key):
                setattr(instance, key, value)

        # Nếu status là COMPLETED hoặc FAILED, set finished_at
        if instance.status in [FlowInstanceStatus.COMPLETED, FlowInstanceStatus.FAILED, FlowInstanceStatus.CANCELLED]:
            if not instance.finished_at:
                instance.finished_at = datetime.now(timezone.utc)
        elif instance.status == FlowInstanceStatus.RUNNING and instance.finished_at:
            # Nếu chuyển về RUNNING, xóa finished_at
            instance.finished_at = None

        await db.commit()
        await db.refresh(instance)

        instance_response = {
            "id": str(instance.id),
            "ticket_id": str(instance.ticket_id),
            "flow_id": str(instance.flow_id),
            "current_step_id": str(instance.current_step_id) if instance.current_step_id else None,
            "status": instance.status.value,
            "started_at": instance.started_at,
            "finished_at": instance.finished_at,
            "tenant_id": str(instance.tenant_id) if instance.tenant_id else None
        }

        return api_response(
            status=ResponseStatus.SUCCESS,
            status_code=ResponseStatusCode.OK,
            message="Cập nhật ticket flow instance thành công",
            data=instance_response
        )

    except IntegrityError as e:
        await db.rollback()
        return api_response(
            status=ResponseStatus.ERROR,
            status_code=ResponseStatusCode.BAD_REQUEST,
            message="Lỗi ràng buộc dữ liệu",
            data=str(e)
        )
    except SQLAlchemyError as e:
        await db.rollback()
        return api_response(
            status=ResponseStatus.ERROR,
            status_code=ResponseStatusCode.INTERNAL_SERVER_ERROR,
            message="Lỗi truy vấn cơ sở dữ liệu",
            data=str(e)
        )
    except Exception as e:
        await db.rollback()
        return api_response(
            status=ResponseStatus.ERROR,
            status_code=ResponseStatusCode.INTERNAL_SERVER_ERROR,
            message="Đã xảy ra lỗi không mong muốn",
            data=str(e)
        )


async def delete_ticket_flow_instance(instance_id: UUID, db: AsyncSession, current_user: User):
    """
    Xóa ticket flow instance (hard delete)
    """
    try:
        # Check quyền super admin
        max_level_user = await is_platform_admin(current_user, db)

        # Query instance
        query = select(TicketFlowInstance).where(TicketFlowInstance.id == instance_id)
        
        # Nếu không phải super admin, chỉ delete được instance của tenant mình
        if not max_level_user:
            query = query.where(TicketFlowInstance.tenant_id == current_user.tenant_id)

        instance = await db.scalar(query)

        if not instance:
            return api_response(
                status=ResponseStatus.ERROR,
                status_code=ResponseStatusCode.NOT_FOUND,
                message="Ticket flow instance không tồn tại hoặc bạn không có quyền xóa"
            )

        # Hard delete
        await db.delete(instance)
        await db.commit()

        return api_response(
            status=ResponseStatus.SUCCESS,
            status_code=ResponseStatusCode.OK,
            message="Xóa ticket flow instance thành công"
        )

    except SQLAlchemyError as e:
        await db.rollback()
        return api_response(
            status=ResponseStatus.ERROR,
            status_code=ResponseStatusCode.INTERNAL_SERVER_ERROR,
            message="Lỗi truy vấn cơ sở dữ liệu",
            data=str(e)
        )
    except Exception as e:
        await db.rollback()
        return api_response(
            status=ResponseStatus.ERROR,
            status_code=ResponseStatusCode.INTERNAL_SERVER_ERROR,
            message="Đã xảy ra lỗi không mong muốn",
            data=str(e)
        )
