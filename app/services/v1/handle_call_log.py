from sqlalchemy.ext.asyncio import AsyncSession
from app.schemas.responses.api_response_rule import api_response, ResponseStatus, ResponseStatusCode
from app.db.models import CallLog, CallLogEvent, User, Tenant, Customer, Ticket
from sqlalchemy import select, func, and_, or_, String
from sqlalchemy.exc import SQLAlchemyError
from app.schemas.requests.call_log import (
    CallLogCreate,
    CallLogUpdate,
    CallLogResponse,
    CallLogEventResponse,
)
from app.utils.helpers import isCheckMaxLevel
from uuid import UUID
from typing import Optional, Any, Tuple
from datetime import datetime, timezone
from sqlalchemy.orm import joinedload, selectinload
import logging

logger = logging.getLogger(__name__)

async def create_call_log(db: AsyncSession, current_user: User, data: CallLogCreate):
    """
    Tạo bản ghi cuộc gọi mới (CallLog)
    """
    try:
        # Xác định tenant_id
        is_super_admin = await isCheckMaxLevel(current_user, db)
        tenant_id = data.tenant_id if (is_super_admin and data.tenant_id) else current_user.tenant_id
        
        if not tenant_id:
            return api_response(
                status=ResponseStatus.ERROR,
                status_code=ResponseStatusCode.BAD_REQUEST,
                message="Tenant ID không hợp lệ hoặc thiếu",
                data=None
            )

        # Kiểm tra trùng sip_call_id
        existing_query = await db.execute(
            select(CallLog).where(CallLog.sip_call_id == data.sip_call_id)
        )
        if existing_query.scalar_one_or_none():
            return api_response(
                status=ResponseStatus.ERROR,
                status_code=ResponseStatusCode.CONFLICT,
                message=f"Đã tồn tại cuộc gọi với sip_call_id: {data.sip_call_id}",
                data=None
            )

        # Tạo bản ghi mới
        new_call = CallLog(
            tenant_id=tenant_id,
            sip_call_id=data.sip_call_id,
            provider_call_id=data.provider_call_id,
            customer_id=data.customer_id,
            ticket_id=data.ticket_id,
            user_id=data.user_id or current_user.id,
            direction=data.direction,
            phone_number=data.phone_number,
            from_number=data.from_number,
            to_number=data.to_number,
            hotline=data.hotline,
            status=data.status or "created",
            source=data.source or "web",
            started_at=data.started_at or datetime.now(timezone.utc),
            answered_at=data.answered_at,
            ended_at=data.ended_at,
            duration=data.duration or 0,
            billsec=data.billsec or 0,
            recording_url=data.recording_url,
            meta_data=data.meta_data,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )

        db.add(new_call)
        await db.commit()
        await db.refresh(new_call)

        return api_response(
            status=ResponseStatus.SUCCESS,
            status_code=ResponseStatusCode.CREATED,
            message="Tạo bản ghi cuộc gọi thành công",
            data=CallLogResponse.model_validate(new_call)
        )

    except SQLAlchemyError as e:
        await db.rollback()
        logger.error(f"[DB ERROR] create_call_log: {e}")
        return api_response(
            status=ResponseStatus.ERROR,
            status_code=ResponseStatusCode.INTERNAL_SERVER_ERROR,
            message="Lỗi cơ sở dữ liệu khi tạo cuộc gọi",
            data=None
        )
    except Exception as e:
        logger.error(f"[ERROR] create_call_log: {e}")
        return api_response(
            status=ResponseStatus.ERROR,
            status_code=ResponseStatusCode.INTERNAL_SERVER_ERROR,
            message=f"Lỗi không xác định: {str(e)}",
            data=None
        )

async def update_call_log(sip_call_id: UUID, db: AsyncSession, current_user: User, data: CallLogUpdate):
    """
    Cập nhật trạng thái cuộc gọi theo sip_call_id
    """
    try:
        # Tìm bản ghi cuộc gọi
        query = await db.execute(
            select(CallLog).where(CallLog.sip_call_id == sip_call_id)
        )
        call_log = query.scalar_one_or_none()
        if not call_log:
            return api_response(
                status=ResponseStatus.ERROR,
                status_code=ResponseStatusCode.NOT_FOUND,
                message=f"Không tìm thấy cuộc gọi với sip_call_id: {sip_call_id}",
                data=None
            )

        # Kiểm tra quyền hạn (phải thuộc cùng tenant hoặc là Super Admin)
        is_super_admin = await isCheckMaxLevel(current_user, db)
        if not is_super_admin and call_log.tenant_id != current_user.tenant_id:
            return api_response(
                status=ResponseStatus.ERROR,
                status_code=ResponseStatusCode.FORBIDDEN,
                message="Bạn không có quyền cập nhật cuộc gọi của doanh nghiệp khác",
                data=None
            )

        # Cập nhật thông tin
        update_data = data.model_dump(exclude_unset=True)
        for key, value in update_data.items():
            setattr(call_log, key, value)

        # Tự động tính duration nếu kết thúc cuộc gọi
        if "ended_at" in update_data and call_log.ended_at and call_log.started_at:
            if not data.duration:
                diff = call_log.ended_at - call_log.started_at
                call_log.duration = max(0, int(diff.total_seconds()))

        await db.commit()
        await db.refresh(call_log)

        return api_response(
            status=ResponseStatus.SUCCESS,
            status_code=ResponseStatusCode.OK,
            message="Cập nhật cuộc gọi thành công",
            data=CallLogResponse.model_validate(call_log)
        )

    except SQLAlchemyError as e:
        await db.rollback()
        logger.error(f"[DB ERROR] update_call_log: {e}")
        return api_response(
            status=ResponseStatus.ERROR,
            status_code=ResponseStatusCode.INTERNAL_SERVER_ERROR,
            message="Lỗi cơ sở dữ liệu khi cập nhật cuộc gọi",
            data=None
        )
    except Exception as e:
        logger.error(f"[ERROR] update_call_log: {e}")
        return api_response(
            status=ResponseStatus.ERROR,
            status_code=ResponseStatusCode.INTERNAL_SERVER_ERROR,
            message=f"Lỗi không xác định: {str(e)}",
            data=None
        )

async def get_call_log_by_sip_call_id(sip_call_id: UUID, db: AsyncSession, current_user: User):
    """
    Lấy thông tin chi tiết cuộc gọi qua sip_call_id (Hỗ trợ tra cứu ngược)
    """
    try:
        query = await db.execute(
            select(CallLog).where(CallLog.sip_call_id == sip_call_id)
        )
        call_log = query.scalar_one_or_none()
        if not call_log:
            return api_response(
                status=ResponseStatus.ERROR,
                status_code=ResponseStatusCode.NOT_FOUND,
                message=f"Không tìm thấy cuộc gọi với sip_call_id: {sip_call_id}",
                data=None
            )

        # Kiểm tra quyền hạn
        is_super_admin = await isCheckMaxLevel(current_user, db)
        if not is_super_admin and call_log.tenant_id != current_user.tenant_id:
            return api_response(
                status=ResponseStatus.ERROR,
                status_code=ResponseStatusCode.FORBIDDEN,
                message="Bạn không có quyền truy cập cuộc gọi của doanh nghiệp khác",
                data=None
            )

        return api_response(
            status=ResponseStatus.SUCCESS,
            status_code=ResponseStatusCode.OK,
            message="Lấy thông tin cuộc gọi thành công",
            data=CallLogResponse.model_validate(call_log)
        )

    except Exception as e:
        logger.error(f"[ERROR] get_call_log_by_sip_call_id: {e}")
        return api_response(
            status=ResponseStatus.ERROR,
            status_code=ResponseStatusCode.INTERNAL_SERVER_ERROR,
            message=f"Lỗi không xác định: {str(e)}",
            data=None
        )

async def get_call_logs(
    db: AsyncSession,
    current_user: User,
    page: int = 1,
    page_size: int = 10,
    search: Optional[str] = None,
    direction: Optional[str] = None,
    status: Optional[str] = None,
    tenant_id: Optional[UUID] = None,
    ticket_id: Optional[UUID] = None,
    customer_id: Optional[UUID] = None
):
    try:
        is_super_admin = await isCheckMaxLevel(current_user, db)

        query = select(CallLog).options(
            selectinload(CallLog.tenant),
            selectinload(CallLog.user),
        )

        filters = []

        if is_super_admin and tenant_id:
            filters.append(CallLog.tenant_id == tenant_id)
        elif not is_super_admin:
            filters.append(CallLog.tenant_id == current_user.tenant_id)

        if direction:
            filters.append(CallLog.direction == direction)
        if status:
            filters.append(CallLog.status == status)
        if ticket_id:
            filters.append(CallLog.ticket_id == ticket_id)
        if customer_id:
            filters.append(CallLog.customer_id == customer_id)
        if search:
            filters.append(
                or_(
                    CallLog.phone_number.ilike(f"%{search}%"),
                    CallLog.from_number.ilike(f"%{search}%"),
                    CallLog.to_number.ilike(f"%{search}%"),
                    CallLog.sip_call_id.cast(String).ilike(f"%{search}%"),
                )
            )

        if filters:
            query = query.where(and_(*filters))

        count_query = select(func.count()).select_from(CallLog)
        if filters:
            count_query = count_query.where(and_(*filters))

        total_count_result = await db.execute(count_query)
        total_count = total_count_result.scalar() or 0

        total_pages = (total_count + page_size - 1) // page_size
        offset = (page - 1) * page_size
        query = query.order_by(CallLog.created_at.desc()).offset(offset).limit(page_size)

        result = await db.execute(query)
        call_logs = result.scalars().all()

        items = []
        for log in call_logs:
            items.append({
                "id": str(log.id),
                "sip_call_id": str(log.sip_call_id),
                "provider_call_id": str(log.provider_call_id) if log.provider_call_id else None,
                "tenant_id": str(log.tenant_id),
                "tenant_name": log.tenant.name if log.tenant else None,
                "username_action_call": log.user.username if log.user else None,
                "customer_id": str(log.customer_id) if log.customer_id else None,
                "ticket_id": str(log.ticket_id) if log.ticket_id else None,
                "user_id": str(log.user_id) if log.user_id else None,
                "duration": log.duration,
                "billsec": log.billsec,
                "started_at": log.started_at.isoformat() if log.started_at else None,
                "answered_at": log.answered_at.isoformat() if log.answered_at else None,
                "ended_at": log.ended_at.isoformat() if log.ended_at else None,
                "meta_data": log.meta_data,
                "phone_number": log.phone_number,
                "from_number": log.from_number,
                "to_number": log.to_number,
                "hotline": log.hotline,
                "status": log.status,
                "direction": log.direction,
                "source": log.source,
                "recording_url": log.recording_url,
                "created_at": log.created_at.isoformat() if log.created_at else None,
            })

        return api_response(
            status=ResponseStatus.SUCCESS,
            status_code=ResponseStatusCode.OK,
            message="Lấy danh sách cuộc gọi thành công",
            data={
                "total": total_count,
                "page": page,
                "page_size": page_size,
                "total_pages": total_pages,
                "items": items
            }
        )

    except Exception as e:
        logger.error(f"[ERROR] get_call_logs: {e}")
        return api_response(
            status=ResponseStatus.ERROR,
            status_code=ResponseStatusCode.INTERNAL_SERVER_ERROR,
            message=f"Lỗi không xác định: {str(e)}",
            data=None
        )


async def _get_tenant_scoped_call_log(
    db: AsyncSession,
    current_user: User,
    sip_call_id: UUID,
) -> Tuple[Optional[CallLog], Optional[Any]]:
    """Trả (call_log, error_response). error_response != None nếu không được phép / không tìm thấy."""
    query = await db.execute(select(CallLog).where(CallLog.sip_call_id == sip_call_id))
    call_log = query.scalar_one_or_none()
    if not call_log:
        return None, api_response(
            status=ResponseStatus.ERROR,
            status_code=ResponseStatusCode.NOT_FOUND,
            message=f"Không tìm thấy cuộc gọi với sip_call_id: {sip_call_id}",
            data=None,
        )

    is_super_admin = await isCheckMaxLevel(current_user, db)
    if not is_super_admin and call_log.tenant_id != current_user.tenant_id:
        return None, api_response(
            status=ResponseStatus.ERROR,
            status_code=ResponseStatusCode.FORBIDDEN,
            message="Bạn không có quyền truy cập cuộc gọi của doanh nghiệp khác",
            data=None,
        )
    return call_log, None


async def get_call_log_events(
    sip_call_id: UUID,
    db: AsyncSession,
    current_user: User,
    page: int = 1,
    page_size: int = 50,
    state: Optional[str] = None,
):
    """Timeline event của 1 cuộc gọi (append-only raw webhook)."""
    try:
        call_log, err = await _get_tenant_scoped_call_log(db, current_user, sip_call_id)
        if err:
            return err

        filters = [CallLogEvent.call_log_id == call_log.id]
        if state:
            filters.append(CallLogEvent.state == state.lower().strip())

        count_q = await db.execute(
            select(func.count()).select_from(CallLogEvent).where(and_(*filters))
        )
        total = count_q.scalar() or 0
        total_pages = (total + page_size - 1) // page_size if page_size else 0
        offset = (page - 1) * page_size

        result = await db.execute(
            select(CallLogEvent)
            .where(and_(*filters))
            .order_by(CallLogEvent.received_at.asc())
            .offset(offset)
            .limit(page_size)
        )
        events = result.scalars().all()

        return api_response(
            status=ResponseStatus.SUCCESS,
            status_code=ResponseStatusCode.OK,
            message="Lấy danh sách call events thành công",
            data={
                "sip_call_id": str(call_log.sip_call_id),
                "call_log_id": str(call_log.id),
                "total": total,
                "page": page,
                "page_size": page_size,
                "total_pages": total_pages,
                "items": [
                    CallLogEventResponse.model_validate(e).model_dump(mode="json")
                    for e in events
                ],
            },
        )
    except Exception as e:
        logger.error(f"[ERROR] get_call_log_events: {e}")
        return api_response(
            status=ResponseStatus.ERROR,
            status_code=ResponseStatusCode.INTERNAL_SERVER_ERROR,
            message=f"Lỗi không xác định: {str(e)}",
            data=None,
        )


async def get_call_log_event_by_id(
    sip_call_id: UUID,
    event_id: UUID,
    db: AsyncSession,
    current_user: User,
):
    """Chi tiết 1 event theo id (trong phạm vi cuộc gọi)."""
    try:
        call_log, err = await _get_tenant_scoped_call_log(db, current_user, sip_call_id)
        if err:
            return err

        result = await db.execute(
            select(CallLogEvent).where(
                CallLogEvent.id == event_id,
                CallLogEvent.call_log_id == call_log.id,
            )
        )
        event = result.scalar_one_or_none()
        if not event:
            return api_response(
                status=ResponseStatus.ERROR,
                status_code=ResponseStatusCode.NOT_FOUND,
                message="Không tìm thấy call event",
                data=None,
            )

        return api_response(
            status=ResponseStatus.SUCCESS,
            status_code=ResponseStatusCode.OK,
            message="Lấy call event thành công",
            data=CallLogEventResponse.model_validate(event).model_dump(mode="json"),
        )
    except Exception as e:
        logger.error(f"[ERROR] get_call_log_event_by_id: {e}")
        return api_response(
            status=ResponseStatus.ERROR,
            status_code=ResponseStatusCode.INTERNAL_SERVER_ERROR,
            message=f"Lỗi không xác định: {str(e)}",
            data=None,
        )