from sqlalchemy.ext.asyncio import AsyncSession
from app.schemas.responses.api_response_rule import api_response, ResponseStatus, ResponseStatusCode
from app.db.models import CallLog, CallLogEvent, User, Customer, Ticket
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
from sqlalchemy.orm import selectinload
import logging

logger = logging.getLogger(__name__)


async def _validate_call_log_refs(
    db: AsyncSession,
    tenant_id: UUID,
    *,
    customer_id: Optional[UUID] = None,
    ticket_id: Optional[UUID] = None,
    user_id: Optional[UUID] = None,
) -> Optional[Any]:
    """
    Đảm bảo customer/ticket/user (nếu có) tồn tại và thuộc cùng tenant với call log.
    User phải bật call_log_enabled.
    Trả về api_response lỗi, hoặc None nếu hợp lệ.
    """
    if customer_id is not None:
        customer = await db.scalar(
            select(Customer.id).where(
                Customer.id == customer_id,
                Customer.tenant_id == tenant_id,
            )
        )
        if not customer:
            return api_response(
                status=ResponseStatus.ERROR,
                status_code=ResponseStatusCode.BAD_REQUEST,
                message="customer_id không tồn tại hoặc không thuộc tenant của cuộc gọi",
                data=None,
            )

    if ticket_id is not None:
        ticket = await db.scalar(
            select(Ticket.id).where(
                Ticket.id == ticket_id,
                Ticket.tenant_id == tenant_id,
            )
        )
        if not ticket:
            return api_response(
                status=ResponseStatus.ERROR,
                status_code=ResponseStatusCode.BAD_REQUEST,
                message="ticket_id không tồn tại hoặc không thuộc tenant của cuộc gọi",
                data=None,
            )

    if user_id is not None:
        user = await db.scalar(
            select(User).where(
                User.id == user_id,
                User.tenant_id == tenant_id,
            )
        )
        if not user:
            return api_response(
                status=ResponseStatus.ERROR,
                status_code=ResponseStatusCode.BAD_REQUEST,
                message="user_id không tồn tại hoặc không thuộc tenant của cuộc gọi",
                data=None,
            )
        if user.call_log_enabled is False:
            return api_response(
                status=ResponseStatus.ERROR,
                status_code=ResponseStatusCode.BAD_REQUEST,
                message="user_id đã tắt ghi log cuộc gọi (call_log_enabled=false)",
                data=None,
            )

    return None


def _serialize_call_log(log: CallLog) -> dict:
    """Serialize CallLog → dict thống nhất cho create/get/list."""
    data = CallLogResponse.model_validate(log).model_dump(mode="json")
    tenant = getattr(log, "tenant", None)
    user = getattr(log, "user", None)
    data["tenant_name"] = tenant.name if tenant is not None else None
    data["username_action_call"] = user.username if user is not None else None
    return data


async def _load_call_log_with_rels(db: AsyncSession, call_log_id: UUID) -> Optional[CallLog]:
    result = await db.execute(
        select(CallLog)
        .options(selectinload(CallLog.tenant), selectinload(CallLog.user))
        .where(CallLog.id == call_log_id)
    )
    return result.scalar_one_or_none()


async def create_call_log(db: AsyncSession, current_user: User, data: CallLogCreate):
    """
    Tạo bản ghi cuộc gọi mới (CallLog)
    """
    try:
        if current_user.call_log_enabled is False:
            return api_response(
                status=ResponseStatus.ERROR,
                status_code=ResponseStatusCode.FORBIDDEN,
                message="Tài khoản của bạn đã tắt ghi log cuộc gọi",
                data=None,
            )

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

        if data.user_id:
            resolved_user_id = data.user_id
        elif current_user.tenant_id == tenant_id:
            resolved_user_id = current_user.id
        else:
            # Super admin tạo hộ tenant khác mà không chỉ định agent
            resolved_user_id = None

        fk_err = await _validate_call_log_refs(
            db,
            tenant_id,
            customer_id=data.customer_id,
            ticket_id=data.ticket_id,
            user_id=resolved_user_id,
        )
        if fk_err:
            return fk_err

        # Tạo bản ghi mới
        new_call = CallLog(
            tenant_id=tenant_id,
            sip_call_id=data.sip_call_id,
            provider_call_id=data.provider_call_id,
            customer_id=data.customer_id,
            ticket_id=data.ticket_id,
            user_id=resolved_user_id,
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
        loaded = await _load_call_log_with_rels(db, new_call.id)

        return api_response(
            status=ResponseStatus.SUCCESS,
            status_code=ResponseStatusCode.CREATED,
            message="Tạo bản ghi cuộc gọi thành công",
            data=_serialize_call_log(loaded or new_call),
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
        if any(k in update_data for k in ("customer_id", "ticket_id", "user_id")):
            fk_err = await _validate_call_log_refs(
                db,
                call_log.tenant_id,
                customer_id=update_data["customer_id"] if update_data.get("customer_id") else None,
                ticket_id=update_data["ticket_id"] if update_data.get("ticket_id") else None,
                user_id=update_data["user_id"] if update_data.get("user_id") else None,
            )
            if fk_err:
                return fk_err

        for key, value in update_data.items():
            setattr(call_log, key, value)

        # Tự động tính duration nếu kết thúc cuộc gọi
        if "ended_at" in update_data and call_log.ended_at and call_log.started_at:
            if not data.duration:
                diff = call_log.ended_at - call_log.started_at
                call_log.duration = max(0, int(diff.total_seconds()))

        await db.commit()
        loaded = await _load_call_log_with_rels(db, call_log.id)

        return api_response(
            status=ResponseStatus.SUCCESS,
            status_code=ResponseStatusCode.OK,
            message="Cập nhật cuộc gọi thành công",
            data=_serialize_call_log(loaded or call_log),
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
            select(CallLog)
            .options(selectinload(CallLog.tenant), selectinload(CallLog.user))
            .where(CallLog.sip_call_id == sip_call_id)
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
            data=_serialize_call_log(call_log),
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

        items = [_serialize_call_log(log) for log in call_logs]

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