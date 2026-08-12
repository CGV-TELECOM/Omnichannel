from sqlalchemy.ext.asyncio import AsyncSession
from app.schemas.responses.api_response_rule import api_response, ResponseStatus, ResponseStatusCode
from app.db.models import TicketFlowStep, TicketFlow, User, Group, Tenant
from sqlalchemy import select, func, or_, and_
from sqlalchemy.exc import SQLAlchemyError, IntegrityError
from app.schemas.requests.ticket_flow_step import TicketFlowStepCreate, TicketFlowStepUpdate
from app.utils.helpers import is_platform_admin
from uuid import UUID
from sqlalchemy.orm import selectinload
from datetime import datetime, timezone


async def get_ticket_flow_steps(
    db: AsyncSession,
    current_user: User,
    id: UUID | None = None,
    flow_id: UUID | None = None,
    page: int = 1,
    page_size: int = 10,
    sort_by: str | None = None,
    sort_order: str = "asc"
):
    """
    Lấy danh sách ticket flow steps với phân trang và filter
    """
    try:
        # Nếu có ID cụ thể, trả về step đó
        if id:
            return await get_ticket_flow_step_by_id(id, db, current_user)

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

        # Query base với join flow để filter theo tenant
        base_query = (
            select(TicketFlowStep)
            .join(TicketFlow)
            .options(
                selectinload(TicketFlowStep.flow),
                selectinload(TicketFlowStep.assignee_user),
                selectinload(TicketFlowStep.assignee_group),
            )
        )
        # Filter theo tenant - user chỉ thấy steps của flows thuộc tenant mình
        if not max_level_user:
            base_query = base_query.where(TicketFlow.tenant_id == current_user.tenant_id)

        # Filter theo flow_id
        if flow_id:
            base_query = base_query.where(TicketFlowStep.flow_id == flow_id)

        # Sort
        if sort_by and hasattr(TicketFlowStep, sort_by):
            sort_col = getattr(TicketFlowStep, sort_by)
            base_query = base_query.order_by(
                sort_col.desc() if sort_order.lower() == "desc" else sort_col.asc()
            )
        else:
            # Mặc định sắp xếp theo step_order
            base_query = base_query.order_by(TicketFlowStep.step_order.asc())

        # Count total
        count_query = select(func.count()).select_from(base_query.subquery())
        total_count = await db.scalar(count_query) or 0
        total_pages = (total_count + page_size - 1) // page_size

        # Pagination
        base_query = base_query.offset((page - 1) * page_size).limit(page_size)

        # Execute
        results = await db.execute(base_query)
        steps = results.scalars().all()

        # Format data
        step_list = []
        for step in steps:
            step_data = {
                "id": str(step.id),
                "flow_id": str(step.flow_id),
                "step_name": step.step_name,
                "step_order": step.step_order,
                "assignee_user_id": str(step.assignee_user_id) if step.assignee_user_id else None,
                "assignee_group_id": str(step.assignee_group_id) if step.assignee_group_id else None,
                "created_at": step.created_at
            }
            
            # Thêm thông tin flow nếu có
            if step.flow:
                step_data["flow"] = {
                    "id": str(step.flow.id),
                    "name": step.flow.name,
                    "description": step.flow.description
                }
            
            # Thêm thông tin assignee_user nếu có
            if step.assignee_user:
                step_data["assignee_user"] = {
                    "id": str(step.assignee_user.id),
                    "username": step.assignee_user.username,
                    "fullname": step.assignee_user.fullname
                }
            
            # Thêm thông tin assignee_group nếu có
            if step.assignee_group:
                step_data["assignee_group"] = {
                    "id": str(step.assignee_group.id),
                    "name": step.assignee_group.name,
                    "description": step.assignee_group.description
                }
            
            step_list.append(step_data)

        return api_response(
            status=ResponseStatus.SUCCESS,
            status_code=ResponseStatusCode.OK,
            message="Lấy danh sách ticket flow steps thành công",
            data={
                "steps": step_list,
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


async def get_ticket_flow_step_by_id(step_id: UUID, db: AsyncSession, current_user: User):
    """
    Lấy thông tin chi tiết một ticket flow step theo ID
    """
    try:
        # Check quyền super admin
        max_level_user = await is_platform_admin(current_user, db)

        # Query step với relationships
        query = select(TicketFlowStep).where(TicketFlowStep.id == step_id)
        
        # Nếu không phải super admin, chỉ xem được step của flow thuộc tenant mình
        if not max_level_user:
            query = query.join(TicketFlow).where(TicketFlow.tenant_id == current_user.tenant_id)

        step = await db.scalar(query)

        if not step:
            return api_response(
                status=ResponseStatus.ERROR,
                status_code=ResponseStatusCode.NOT_FOUND,
                message="Ticket flow step không tồn tại hoặc bạn không có quyền truy cập"
            )

        # Format data với đầy đủ thông tin
        step_data = {
            "id": str(step.id),
            "flow_id": str(step.flow_id),
            "step_name": step.step_name,
            "step_order": step.step_order,
            "assignee_user_id": str(step.assignee_user_id) if step.assignee_user_id else None,
            "assignee_group_id": str(step.assignee_group_id) if step.assignee_group_id else None,
            "created_at": step.created_at
        }

        # Thêm thông tin flow
        if step.flow:
            step_data["flow"] = {
                "id": str(step.flow.id),
                "name": step.flow.name,
                "description": step.flow.description,
                "tenant_id": str(step.flow.tenant_id)
            }

        # Thêm thông tin assignee_user
        if step.assignee_user:
            step_data["assignee_user"] = {
                "id": str(step.assignee_user.id),
                "username": step.assignee_user.username,
                "fullname": step.assignee_user.fullname,
                "email": step.assignee_user.email
            }

        # Thêm thông tin assignee_group
        if step.assignee_group:
            step_data["assignee_group"] = {
                "id": str(step.assignee_group.id),
                "name": step.assignee_group.name,
                "description": step.assignee_group.description
            }

        return api_response(
            status=ResponseStatus.SUCCESS,
            status_code=ResponseStatusCode.OK,
            message="Lấy thông tin ticket flow step thành công",
            data=step_data
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


async def create_ticket_flow_step(step_data: TicketFlowStepCreate, db: AsyncSession, current_user: User):
    """
    Tạo ticket flow step mới
    """
    try:
        # Check quyền super admin
        max_level_user = await is_platform_admin(current_user, db)

        # Validate flow tồn tại và thuộc tenant
        flow_query = select(TicketFlow).where(TicketFlow.id == step_data.flow_id)
        if not max_level_user:
            flow_query = flow_query.where(TicketFlow.tenant_id == current_user.tenant_id)

        flow = await db.scalar(flow_query)
        if not flow:
            return api_response(
                status=ResponseStatus.ERROR,
                status_code=ResponseStatusCode.NOT_FOUND,
                message="Flow không tồn tại hoặc không thuộc tenant của bạn"
            )

        # Validate assignee_user_id nếu có
        if step_data.assignee_user_id:
            user = await db.scalar(
                select(User).where(
                    User.id == step_data.assignee_user_id,
                    User.tenant_id == flow.tenant_id
                )
            )
            if not user:
                return api_response(
                    status=ResponseStatus.ERROR,
                    status_code=ResponseStatusCode.NOT_FOUND,
                    message="User không tồn tại hoặc không thuộc tenant của flow"
                )

        # Validate assignee_group_id nếu có
        if step_data.assignee_group_id:
            group = await db.scalar(
                select(Group).where(
                    Group.id == step_data.assignee_group_id,
                    Group.tenant_id == flow.tenant_id
                )
            )
            if not group:
                return api_response(
                    status=ResponseStatus.ERROR,
                    status_code=ResponseStatusCode.NOT_FOUND,
                    message="Group không tồn tại hoặc không thuộc tenant của flow"
                )

        # Kiểm tra step_order không trùng trong cùng flow
        existing_step = await db.scalar(
            select(TicketFlowStep).where(
                TicketFlowStep.flow_id == step_data.flow_id,
                TicketFlowStep.step_order == step_data.step_order
            )
        )
        if existing_step:
            return api_response(
                status=ResponseStatus.ERROR,
                status_code=ResponseStatusCode.BAD_REQUEST,
                message=f"Step với step_order {step_data.step_order} đã tồn tại trong flow này"
            )

        # Tạo step mới
        new_step = TicketFlowStep(
            flow_id=step_data.flow_id,
            step_name=step_data.step_name,
            step_order=step_data.step_order,
            assignee_user_id=step_data.assignee_user_id,
            assignee_group_id=step_data.assignee_group_id
        )

        db.add(new_step)
        await db.commit()
        await db.refresh(new_step)

        step_response = {
            "id": str(new_step.id),
            "flow_id": str(new_step.flow_id),
            "step_name": new_step.step_name,
            "step_order": new_step.step_order,
            "assignee_user_id": str(new_step.assignee_user_id) if new_step.assignee_user_id else None,
            "assignee_group_id": str(new_step.assignee_group_id) if new_step.assignee_group_id else None,
            "created_at": new_step.created_at
        }

        return api_response(
            status=ResponseStatus.SUCCESS,
            status_code=ResponseStatusCode.CREATED,
            message="Tạo ticket flow step thành công",
            data=step_response
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


async def update_ticket_flow_step(step_id: UUID, step_data: TicketFlowStepUpdate, db: AsyncSession, current_user: User):
    """
    Cập nhật thông tin ticket flow step
    """
    try:
        # Check quyền super admin
        max_level_user = await is_platform_admin(current_user, db)

        # Query step với join flow để check tenant
        query = (
            select(TicketFlowStep)
            .options(selectinload(TicketFlowStep.flow))
            .where(TicketFlowStep.id == step_id)
        )

        if not max_level_user:
            query = query.join(TicketFlow).where(
                TicketFlow.tenant_id == current_user.tenant_id
            )

        step = await db.scalar(query)

        if not step:
            return api_response(
                status=ResponseStatus.ERROR,
                status_code=ResponseStatusCode.NOT_FOUND,
                message="Ticket flow step không tồn tại hoặc bạn không có quyền cập nhật"
            )

        # Validate assignee_user_id nếu có
        if step_data.assignee_user_id is not None:
            if step_data.assignee_user_id:
                user = await db.scalar(
                    select(User).where(
                        User.id == step_data.assignee_user_id,
                        User.tenant_id == step.flow.tenant_id
                    )
                )
                if not user:
                    return api_response(
                        status=ResponseStatus.ERROR,
                        status_code=ResponseStatusCode.NOT_FOUND,
                        message="User không tồn tại hoặc không thuộc tenant của flow"
                    )

        # Validate assignee_group_id nếu có
        if step_data.assignee_group_id is not None:
            if step_data.assignee_group_id:
                group = await db.scalar(
                    select(Group).where(
                        Group.id == step_data.assignee_group_id,
                        Group.tenant_id == step.flow.tenant_id
                    )
                )
                if not group:
                    return api_response(
                        status=ResponseStatus.ERROR,
                        status_code=ResponseStatusCode.NOT_FOUND,
                        message="Group không tồn tại hoặc không thuộc tenant của flow"
                    )

        # Kiểm tra step_order không trùng trong cùng flow (nếu đổi step_order)
        if step_data.step_order and step_data.step_order != step.step_order:
            existing_step = await db.scalar(
                select(TicketFlowStep).where(
                    TicketFlowStep.flow_id == step.flow_id,
                    TicketFlowStep.step_order == step_data.step_order,
                    TicketFlowStep.id != step_id
                )
            )
            if existing_step:
                return api_response(
                    status=ResponseStatus.ERROR,
                    status_code=ResponseStatusCode.BAD_REQUEST,
                    message=f"Step với step_order {step_data.step_order} đã tồn tại trong flow này"
                )

        # Update các field
        update_data = step_data.model_dump(exclude_unset=True)
        
        for key, value in update_data.items():
            if hasattr(step, key):
                setattr(step, key, value)

        await db.commit()
        await db.refresh(step)

        step_response = {
            "id": str(step.id),
            "flow_id": str(step.flow_id),
            "step_name": step.step_name,
            "step_order": step.step_order,
            "assignee_user_id": str(step.assignee_user_id) if step.assignee_user_id else None,
            "assignee_group_id": str(step.assignee_group_id) if step.assignee_group_id else None,
            "created_at": step.created_at
        }

        return api_response(
            status=ResponseStatus.SUCCESS,
            status_code=ResponseStatusCode.OK,
            message="Cập nhật ticket flow step thành công",
            data=step_response
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


async def delete_ticket_flow_step(step_id: UUID, db: AsyncSession, current_user: User):
    """
    Xóa ticket flow step (hard delete)
    """
    try:
        # Check quyền super admin
        max_level_user = await is_platform_admin(current_user, db)

        # Query step với join flow để check tenant
        query = select(TicketFlowStep).where(TicketFlowStep.id == step_id)
        if not max_level_user:
            query = query.join(TicketFlow).where(TicketFlow.tenant_id == current_user.tenant_id)

        step = await db.scalar(query)

        if not step:
            return api_response(
                status=ResponseStatus.ERROR,
                status_code=ResponseStatusCode.NOT_FOUND,
                message="Ticket flow step không tồn tại hoặc bạn không có quyền xóa"
            )

        # Kiểm tra xem step có đang được sử dụng trong flow instances không
        from app.db.models import TicketFlowInstance
        instances_using_step = await db.scalar(
            select(func.count()).select_from(
                select(TicketFlowInstance).where(
                    TicketFlowInstance.current_step_id == step_id
                ).subquery()
            )
        ) or 0

        if instances_using_step > 0:
            return api_response(
                status=ResponseStatus.ERROR,
                status_code=ResponseStatusCode.BAD_REQUEST,
                message=f"Không thể xóa step này vì đang có {instances_using_step} flow instance(s) đang sử dụng"
            )

        # Hard delete
        await db.delete(step)
        await db.commit()

        return api_response(
            status=ResponseStatus.SUCCESS,
            status_code=ResponseStatusCode.OK,
            message="Xóa ticket flow step thành công"
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
