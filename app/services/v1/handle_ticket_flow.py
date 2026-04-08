from sqlalchemy.ext.asyncio import AsyncSession
from app.schemas.responses.api_response_rule import api_response, ResponseStatus, ResponseStatusCode
from app.db.models import TicketFlow, User, Tenant
from sqlalchemy import select, func, or_, and_
from sqlalchemy.exc import SQLAlchemyError, IntegrityError
from sqlalchemy.orm import selectinload
from app.schemas.requests.ticket_flow import TicketFlowCreate, TicketFlowUpdate
from app.utils.helpers import isCheckMaxLevel, isCheckMaxLevelTenant
from uuid import UUID
from datetime import datetime, timezone


async def get_ticket_flows(
    db: AsyncSession,
    current_user: User,
    id: UUID | None = None,
    page: int = 1,
    page_size: int = 10,
    search: str | None = None,
    sort_by: str | None = None,
    sort_order: str = "asc"
):
    """
    Lấy danh sách ticket flows với phân trang, tìm kiếm và filter theo tenant
    """
    try:
        # Nếu có ID cụ thể, trả về flow đó
        if id:
            return await get_ticket_flow_by_id(id, db, current_user)

        # Check quyền super admin (level cao nhất)
        max_level_user = await isCheckMaxLevel(current_user, db)

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
            select(TicketFlow)
            .options(
                selectinload(TicketFlow.steps),
                selectinload(TicketFlow.tickets)
            )
        )

        # Filter theo tenant - user chỉ thấy flows của tenant mình
        if not max_level_user:
            base_query = base_query.where(TicketFlow.tenant_id == current_user.tenant_id)

        # Filter search (tìm theo tên hoặc mô tả)
        if search:
            like_search = f"%{search}%"
            base_query = base_query.where(
                or_(
                    TicketFlow.name.ilike(like_search),
                    TicketFlow.description.ilike(like_search)
                )
            )

        # Sort
        if sort_by and hasattr(TicketFlow, sort_by):
            sort_col = getattr(TicketFlow, sort_by)
            base_query = base_query.order_by(
                sort_col.desc() if sort_order.lower() == "desc" else sort_col.asc()
            )
        else:
            # Mặc định sắp xếp theo tên
            base_query = base_query.order_by(TicketFlow.name.asc())

        # Count total
        count_query = select(func.count()).select_from(base_query.subquery())
        total_count = await db.scalar(count_query) or 0
        total_pages = (total_count + page_size - 1) // page_size

        # Pagination
        base_query = base_query.offset((page - 1) * page_size).limit(page_size)

        # Execute
        results = await db.execute(base_query)
        flows = results.scalars().all()

        # Format data
        flow_list = [
            {
                "id": str(flow.id),
                "tenant_id": str(flow.tenant_id),
                "name": flow.name,
                "description": flow.description,
                "created_at": flow.created_at,
                "updated_at": flow.updated_at,
                "steps_count": len(flow.steps) if flow.steps else 0,
                "tickets_count": len(flow.tickets) if flow.tickets else 0
            }
            for flow in flows
        ]

        return api_response(
            status=ResponseStatus.SUCCESS,
            status_code=ResponseStatusCode.OK,
            message="Lấy danh sách ticket flows thành công",
            data={
                "flows": flow_list,
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


async def get_ticket_flow_by_id(flow_id: UUID, db: AsyncSession, current_user: User):
    """
    Lấy thông tin chi tiết một ticket flow theo ID
    """
    try:
        # Check quyền super admin
        max_level_user = await isCheckMaxLevel(current_user, db)

        # Query flow
        query = (
            select(TicketFlow)
            .options(
                selectinload(TicketFlow.steps),
                selectinload(TicketFlow.tickets),
                selectinload(TicketFlow.flow_instances),
            )
            .where(TicketFlow.id == flow_id)
        )        
        # Nếu không phải super admin, chỉ xem được flow của tenant mình
        if not max_level_user:
            query = query.where(TicketFlow.tenant_id == current_user.tenant_id)

        flow = await db.scalar(query)

        if not flow:
            return api_response(
                status=ResponseStatus.ERROR,
                status_code=ResponseStatusCode.NOT_FOUND,
                message="Ticket flow không tồn tại hoặc bạn không có quyền truy cập"
            )

        # Format steps
        steps = flow.steps if flow.steps else []
        steps_list = [
            {
                "id": str(step.id),
                "step_name": step.step_name,
                "step_order": step.step_order,
                "assignee_user_id": str(step.assignee_user_id) if step.assignee_user_id else None,
                "assignee_group_id": str(step.assignee_group_id) if step.assignee_group_id else None,
                "created_at": step.created_at
            }
            for step in sorted(steps, key=lambda x: x.step_order)
        ]

        flow_data = {
            "id": str(flow.id),
            "tenant_id": str(flow.tenant_id),
            "name": flow.name,
            "description": flow.description,
            "created_at": flow.created_at,
            "updated_at": flow.updated_at,
            "steps": steps_list,
            "steps_count": len(flow.steps) if flow.steps else 0,
            "tickets_count": len(flow.tickets) if flow.tickets else 0,
            "flow_instances_count": len(flow.flow_instances) if flow.flow_instances else 0
        }

        return api_response(
            status=ResponseStatus.SUCCESS,
            status_code=ResponseStatusCode.OK,
            message="Lấy thông tin ticket flow thành công",
            data={"flows": [flow_data]}  # Để đồng bộ với định dạng trả về danh sách flows

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


async def create_ticket_flow(flow_data: TicketFlowCreate, db: AsyncSession, current_user: User):
    """
    Tạo ticket flow mới - tự động gán tenant_id từ current_user (trừ super admin)
    """
    try:
        # Check quyền super admin
        max_level_user = await isCheckMaxLevel(current_user, db)

        # Xác định tenant_id
        if max_level_user and flow_data.tenant_id:
            tenant_id = flow_data.tenant_id
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

        # Check trùng tên flow trong cùng tenant
        existing_flow = await db.scalar(
            select(TicketFlow).where(
                TicketFlow.name == flow_data.name,
                TicketFlow.tenant_id == tenant_id
            )
        )
        
        if existing_flow:
            return api_response(
                status=ResponseStatus.ERROR,
                status_code=ResponseStatusCode.BAD_REQUEST,
                message=f"Ticket flow với tên '{flow_data.name}' đã tồn tại trong hệ thống"
            )

        # Tạo flow mới
        new_flow = TicketFlow(
            name=flow_data.name,
            description=flow_data.description,
            tenant_id=tenant_id
        )

        db.add(new_flow)
        await db.commit()
        await db.refresh(new_flow)

        flow_response = {
            "id": str(new_flow.id),
            "tenant_id": str(new_flow.tenant_id),
            "name": new_flow.name,
            "description": new_flow.description,
            "created_at": new_flow.created_at,
            "updated_at": new_flow.updated_at
        }

        return api_response(
            status=ResponseStatus.SUCCESS,
            status_code=ResponseStatusCode.CREATED,
            message="Tạo ticket flow thành công",
            data=flow_response
        )

    except IntegrityError as e:
        await db.rollback()
        return api_response(
            status=ResponseStatus.ERROR,
            status_code=ResponseStatusCode.BAD_REQUEST,
            message="Ticket flow với tên này đã tồn tại",
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


async def update_ticket_flow(flow_id: UUID, flow_data: TicketFlowUpdate, db: AsyncSession, current_user: User):
    """
    Cập nhật thông tin ticket flow
    """
    try:
        # Check quyền super admin
        max_level_user = await isCheckMaxLevel(current_user, db)

        # Query flow
        query = select(TicketFlow).where(TicketFlow.id == flow_id)
        
        # Nếu không phải super admin, chỉ update được flow của tenant mình
        if not max_level_user:
            query = query.where(TicketFlow.tenant_id == current_user.tenant_id)

        flow = await db.scalar(query)

        if not flow:
            return api_response(
                status=ResponseStatus.ERROR,
                status_code=ResponseStatusCode.NOT_FOUND,
                message="Ticket flow không tồn tại hoặc bạn không có quyền cập nhật"
            )

        # Check trùng tên nếu đổi tên
        if flow_data.name and flow_data.name != flow.name:
            existing_flow = await db.scalar(
                select(TicketFlow).where(
                    TicketFlow.name == flow_data.name,
                    TicketFlow.tenant_id == flow.tenant_id,
                    TicketFlow.id != flow_id
                )
            )
            
            if existing_flow:
                return api_response(
                    status=ResponseStatus.ERROR,
                    status_code=ResponseStatusCode.BAD_REQUEST,
                    message=f"Ticket flow với tên '{flow_data.name}' đã tồn tại"
                )

        # Update các field
        update_data = flow_data.model_dump(exclude_unset=True)
        
        for key, value in update_data.items():
            if hasattr(flow, key):
                setattr(flow, key, value)

        # Update timestamp
        flow.updated_at = datetime.now(timezone.utc)

        await db.commit()
        await db.refresh(flow)

        flow_response = {
            "id": str(flow.id),
            "tenant_id": str(flow.tenant_id),
            "name": flow.name,
            "description": flow.description,
            "created_at": flow.created_at,
            "updated_at": flow.updated_at
        }

        return api_response(
            status=ResponseStatus.SUCCESS,
            status_code=ResponseStatusCode.OK,
            message="Cập nhật ticket flow thành công",
            data=flow_response
        )

    except IntegrityError as e:
        await db.rollback()
        return api_response(
            status=ResponseStatus.ERROR,
            status_code=ResponseStatusCode.BAD_REQUEST,
            message="Ticket flow với tên này đã tồn tại",
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


async def delete_ticket_flow(flow_id: UUID, db: AsyncSession, current_user: User):
    """
    Xóa ticket flow (hard delete)
    Lưu ý: Flow có cascade delete với steps và flow_instances
    """
    try:
        # Check quyền super admin
        max_level_user = await isCheckMaxLevel(current_user, db)

        # Query flow
        query = (
            select(TicketFlow)
            .options(selectinload(TicketFlow.tickets))
            .where(TicketFlow.id == flow_id)
        )
        
        # Nếu không phải super admin, chỉ delete được flow của tenant mình
        if not max_level_user:
            query = query.where(TicketFlow.tenant_id == current_user.tenant_id)

        flow = await db.scalar(query)

        if not flow:
            return api_response(
                status=ResponseStatus.ERROR,
                status_code=ResponseStatusCode.NOT_FOUND,
                message="Ticket flow không tồn tại hoặc bạn không có quyền xóa"
            )

        # Kiểm tra xem flow có đang được sử dụng bởi tickets không
        if flow.tickets:
            active_tickets = [t for t in flow.tickets if t.status not in ["closed", "cancelled"]]
            if active_tickets:
                return api_response(
                    status=ResponseStatus.ERROR,
                    status_code=ResponseStatusCode.BAD_REQUEST,
                    message=f"Không thể xóa flow này vì đang có {len(active_tickets)} ticket(s) đang sử dụng"
                )

        # Hard delete (cascade sẽ xóa steps và flow_instances)
        await db.delete(flow)
        await db.commit()

        return api_response(
            status=ResponseStatus.SUCCESS,
            status_code=ResponseStatusCode.OK,
            message="Xóa ticket flow thành công"
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
