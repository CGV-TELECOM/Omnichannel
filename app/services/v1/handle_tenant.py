from sqlalchemy.ext.asyncio import AsyncSession
from app.schemas.responses.api_response_rule import api_response, ResponseStatus, ResponseStatusCode
from app.db.models import (
    Tenant,
    User,
    ChatwootLegacyMap,
    ChatwootMapResourceType,
)
from app.schemas.requests.tenant import TenantCreate, TenantResponse, TenantUpdate 
from sqlalchemy import select, func,  or_
from uuid import UUID
from sqlalchemy.future import select
from fastapi import Request
from app.utils.helpers import isCheckMaxLevel
from sqlalchemy.exc import SQLAlchemyError
from datetime import datetime, timezone
from typing import Any

from app.integrations.chatwoot import client as chatwoot_client
from app.integrations.chatwoot.account_payload import sanitize_platform_account_payload


async def _get_tenant_account_map(
    db: AsyncSession, tenant_id: UUID
) -> ChatwootLegacyMap | None:
    q = await db.execute(
        select(ChatwootLegacyMap).where(
            ChatwootLegacyMap.resource_type == ChatwootMapResourceType.ACCOUNT,
            ChatwootLegacyMap.local_uuid == tenant_id,
        )
    )
    return q.scalar_one_or_none()


def _tenant_chatwoot_account_payload(tenant: Tenant) -> tuple[dict[str, Any], dict[str, Any]]:
    """Merge meta_data (root + chatwoot_account lồng) với name tenant; sanitize Platform API."""
    md = tenant.meta_data if isinstance(tenant.meta_data, dict) else {}
    flat_nested: dict[str, Any] = {}
    cw = md.get("chatwoot_account")
    if isinstance(cw, dict):
        flat_nested = dict(cw)
    root_flat: dict[str, Any] = {}
    for k, v in md.items():
        if k == "chatwoot_account":
            continue
        if v is not None:
            root_flat[k] = v
    # Root meta_data ghi đè nested chatwoot_account (cùng key).
    extra = {**flat_nested, **root_flat}
    extra.pop("name", None)
    raw: dict[str, Any] = {"name": tenant.name, **extra}
    raw.pop("chatwoot_account", None)
    return sanitize_platform_account_payload(raw)


async def getAllTenant(_: Request, current_user: User, id: UUID | None, graph_id: UUID | None, is_active: int | None, graph_activated: int | None, page: int, page_size: int, search: str | None, db: AsyncSession):
    try:
        if not (await isCheckMaxLevel(current_user, db)):
            return api_response(
                ResponseStatus.ERROR,
                ResponseStatusCode.FORBIDDEN,
                "Chỉ quản trị viên mới có thể truy cập tài nguyên này"
        )

        if id:
            query_tenant_raw = select(Tenant).where(Tenant.id == id)
            query_tenant_execute = await db.execute(query_tenant_raw)
            result_tenant = query_tenant_execute.scalar_one_or_none()

            if result_tenant is None:
                return api_response(ResponseStatus.INFO, ResponseStatusCode.BAD_REQUEST, "Tenant không tồn tại")

            return api_response(
                ResponseStatus.SUCCESS,
                ResponseStatusCode.OK,
                "Tìm tenant theo ID thành công",
                TenantResponse(
                    id=result_tenant.id,
                    name=result_tenant.name,
                    description=result_tenant.description,
                    is_active=result_tenant.is_active,
                    meta_data=result_tenant.meta_data,
                    graph_id=result_tenant.graph_id,
                    graph_activated=result_tenant.graph_activated,
                )
            )
        else:
            query = select(Tenant)
            if graph_id:
                query = query.where(Tenant.graph_id == graph_id)
            if is_active is not None:
                query = query.where(Tenant.is_active == is_active)
            if graph_activated is not None:
                query = query.where(Tenant.graph_activated == graph_activated)
            if search:
                search_text = f"%{search}%"
                query = query.where(
                    or_(
                        Tenant.name.ilike(search_text),
                        Tenant.description.ilike(search_text)
                    )
                )

            total_query = select(func.count()).select_from(query.subquery())
            total_result = await db.execute(total_query)
            total = total_result.scalar() or 0
            total_pages = (total + page_size - 1) // page_size

            offset = (page - 1) * page_size
            query = query.offset(offset).limit(page_size)
            result = await db.execute(query)
            tenants = result.scalars().all()

            tenant_list = [
                TenantResponse(
                    id=t.id,
                    name=t.name,
                    description=t.description,
                    is_active=t.is_active,
                    meta_data=t.meta_data,
                    graph_id=t.graph_id,
                    graph_activated=t.graph_activated,
                )
                for t in tenants
            ]

            return api_response(
                ResponseStatus.SUCCESS,
                ResponseStatusCode.OK,
                "Lấy danh sách tenant thành công",
                {
                    "total": total,
                    "page": page,
                    "page_size": page_size,
                    "total_pages": total_pages,
                    "items": tenant_list
                }
            )

    except SQLAlchemyError as e:
        print(f"[DB ERROR] getAllTenant: {e}")
        return api_response(
            ResponseStatus.ERROR,
            ResponseStatusCode.INTERNAL_SERVER_ERROR,
            "Có lỗi xảy ra khi truy vấn cơ sở dữ liệu"
        )
    except Exception as e:
        print(f"[UNEXPECTED ERROR] getAllTenant: {e}")
        return api_response(
            ResponseStatus.ERROR,
            ResponseStatusCode.INTERNAL_SERVER_ERROR,
            "Có lỗi không xác định xảy ra"
        )

async def createTenant(_, current_user: User, tenant_data: TenantCreate, db: AsyncSession):
    try:
        if not (await isCheckMaxLevel(current_user, db)):
            return api_response(
                ResponseStatus.ERROR,
                ResponseStatusCode.FORBIDDEN,
                "Chỉ quản trị viên mới có thể truy cập tài nguyên này"
            )
        # Kiểm tra tên tenant đã tồn tại (không phân biệt hoa thường)
        query_tenant = select(Tenant).where(
            func.upper(Tenant.name) == tenant_data.name.upper()
        )
        tenant_execute = await db.execute(query_tenant)
        tenant_result = tenant_execute.scalar_one_or_none()

        if tenant_result:
            return api_response(
                ResponseStatus.ERROR, 
                ResponseStatusCode.CONFLICT, 
                "Đã tồn tại tên tenant này rồi, vui lòng kiểm tra lại"
            )  

        # Tạo tenant mới
        new_tenant = Tenant(
            name=tenant_data.name,
            description=tenant_data.description,
            meta_data=tenant_data.meta_data,
            graph_id=tenant_data.graph_id,
            graph_activated=tenant_data.graph_activated if tenant_data.graph_activated is not None else 0,
        )
        db.add(new_tenant)
        await db.flush()

        chatwoot_payload, _ = _tenant_chatwoot_account_payload(new_tenant)
        cw_res = await chatwoot_client.platform_request(
            "POST",
            "/platform/api/v1/accounts",
            json_body=chatwoot_payload,
        )
        if (
            cw_res.status_code not in (200, 201)
            or not isinstance(cw_res.data, dict)
            or cw_res.data.get("id") is None
        ):
            await db.rollback()
            return api_response(
                ResponseStatus.ERROR,
                cw_res.status_code if cw_res.status_code in (401, 404, 422, 503) else 502,
                "Tạo account Chatwoot thất bại, đã rollback tạo tenant",
                {
                    "chatwoot_status_code": cw_res.status_code,
                    "chatwoot_response": cw_res.data,
                },
            )
        try:
            chatwoot_account_id = int(cw_res.data["id"])
        except (TypeError, ValueError):
            await db.rollback()
            return api_response(
                ResponseStatus.ERROR,
                502,
                "Chatwoot trả id account không hợp lệ, đã rollback tạo tenant",
                {"chatwoot_response": cw_res.data},
            )

        # link integration user
        from app.services.v1.handle_chatwoot._shared import link_integration_user_to_chatwoot_account
        link_info = await link_integration_user_to_chatwoot_account(chatwoot_account_id)
        if not link_info.get("linked"):
            import logging
            logger = logging.getLogger(__name__)
            try:
                await chatwoot_client.platform_request(
                    "DELETE",
                    f"/platform/api/v1/accounts/{chatwoot_account_id}",
                )
            except Exception as delete_ex:
                logger.error(
                    "Lỗi khi xóa account Chatwoot %s sau khi liên kết thất bại: %s",
                    chatwoot_account_id,
                    str(delete_ex),
                )
            await db.rollback()
            msg = "Gắn user tích hợp vào Chatwoot account thất bại, đã rollback tạo doanh nghiệp"
            if link_info.get("skipped_reason"):
                msg += f". Lý do: {link_info.get('skipped_reason')}"
            return api_response(
                ResponseStatus.ERROR,
                502,
                msg,
                {"integration_account_user": link_info},
            )

        db.add(

            ChatwootLegacyMap(
                resource_type=ChatwootMapResourceType.ACCOUNT,
                local_uuid=new_tenant.id,
                chatwoot_id=chatwoot_account_id,
                tenant_id=new_tenant.id,
                created_at=datetime.now(timezone.utc),
            )
        )

        if not isinstance(new_tenant.meta_data, dict):
            new_tenant.meta_data = {}
        new_tenant.meta_data["chatwoot_account"] = dict(chatwoot_payload)

        await db.commit()
        await db.refresh(new_tenant)

        return api_response(
            ResponseStatus.SUCCESS, 
            ResponseStatusCode.CREATED, 
            "Thêm tenant thành công",
            data={
                "tenant": TenantResponse.model_validate(new_tenant),
                "chatwoot_linked": True,
            }
        )

    except Exception as e:
        await db.rollback()
        # Ghi log nếu cần
        print(f"[ERROR] createTenant: {e}")
        return api_response(
            ResponseStatus.ERROR,
            ResponseStatusCode.INTERNAL_SERVER_ERROR,
            "Đã có lỗi xảy ra khi thêm tenant, vui lòng thử lại sau."
        )

async def updateTenant(
    tenant_id: UUID,
    current_user: User,
    _: Request,  
    tenant_data: TenantUpdate,
    db: AsyncSession
):
    try:
        # 1. Kiểm tra quyền
        if not (await isCheckMaxLevel(current_user, db)):
            return api_response(
                ResponseStatus.ERROR,
                ResponseStatusCode.FORBIDDEN,
                "Chỉ quản trị viên mới có thể truy cập tài nguyên này"
            )
        
        # 2. Kiểm tra trùng tên tenant (trừ chính tenant đang cập nhật)
        tenant_query = await db.execute(
            select(Tenant).where(
                func.upper(Tenant.name) == tenant_data.name.upper(),
                Tenant.id != tenant_id
            )
        )
        existing_tenant = tenant_query.scalar_one_or_none()
        if existing_tenant:
            return api_response(
                ResponseStatus.ERROR,
                ResponseStatusCode.CONFLICT,
                "Đã tồn tại tên tenant trong hệ thống"
            )
        
        # 3. Tìm tenant theo ID
        target_query = await db.execute(select(Tenant).where(Tenant.id == tenant_id))
        tenant = target_query.scalar_one_or_none()
        if tenant is None:
            return api_response(
                ResponseStatus.INFO,
                ResponseStatusCode.BAD_REQUEST,
                "Không tìm thấy tenant, vui lòng kiểm tra lại"
            )
        
        # 4. Cập nhật dữ liệu
        for field, value in tenant_data.model_dump(exclude_unset=True).items():
            setattr(tenant, field, value)

        account_map = await _get_tenant_account_map(db, tenant.id)
        if account_map:
            cw_body, _ = _tenant_chatwoot_account_payload(tenant)
            cw_res = await chatwoot_client.platform_request(
                "PATCH",
                f"/platform/api/v1/accounts/{account_map.chatwoot_id}",
                json_body=cw_body,
            )
            if cw_res.status_code != 200:
                await db.rollback()
                return api_response(
                    ResponseStatus.ERROR,
                    cw_res.status_code if cw_res.status_code in (401, 404, 422, 503) else 502,
                    "Cập nhật account Chatwoot thất bại",
                    {
                        "chatwoot_status_code": cw_res.status_code,
                        "chatwoot_response": cw_res.data,
                    },
                )
        else:
            cw_create_body, _ = _tenant_chatwoot_account_payload(tenant)
            cw_create = await chatwoot_client.platform_request(
                "POST",
                "/platform/api/v1/accounts",
                json_body=cw_create_body,
            )
            if (
                cw_create.status_code not in (200, 201)
                or not isinstance(cw_create.data, dict)
                or cw_create.data.get("id") is None
            ):
                await db.rollback()
                return api_response(
                    ResponseStatus.ERROR,
                    cw_create.status_code if cw_create.status_code in (401, 404, 422, 503) else 502,
                    "Tenant chưa có map và tạo account Chatwoot mới thất bại",
                    {
                        "chatwoot_status_code": cw_create.status_code,
                        "chatwoot_response": cw_create.data,
                    },
                )
            try:
                new_account_id = int(cw_create.data["id"])
            except (TypeError, ValueError):
                await db.rollback()
                return api_response(
                    ResponseStatus.ERROR,
                    502,
                    "Chatwoot trả id account không hợp lệ",
                    {"chatwoot_response": cw_create.data},
                )
            db.add(
                ChatwootLegacyMap(
                    resource_type=ChatwootMapResourceType.ACCOUNT,
                    local_uuid=tenant.id,
                    chatwoot_id=new_account_id,
                    tenant_id=tenant.id,
                    created_at=datetime.now(timezone.utc),
                )
            )

        if not isinstance(tenant.meta_data, dict):
            tenant.meta_data = {}
        snap, _ = _tenant_chatwoot_account_payload(tenant)
        tenant.meta_data["chatwoot_account"] = dict(snap)

        await db.commit()
        await db.refresh(tenant)

        return api_response(
            ResponseStatus.SUCCESS,
            ResponseStatusCode.OK,
            "Cập nhật tenant thành công",
            data=TenantResponse(
                id=tenant.id,
                name=tenant.name,
                description=tenant.description,
                is_active=tenant.is_active,
                meta_data=tenant.meta_data,
                graph_id=tenant.graph_id,
                graph_activated=tenant.graph_activated,
            ),
        )

    except SQLAlchemyError as e:
        await db.rollback()
        print(f"[DB ERROR] updateTenant: {e}")
        return api_response(
            ResponseStatus.ERROR, 
            ResponseStatusCode.INTERNAL_SERVER_ERROR, 
            "Có lỗi xảy ra khi thao tác với cơ sở dữ liệu"
        )
    except Exception as e:
        print(f"[UNEXPECTED ERROR] updateTenant: {e}")
        return api_response(
            ResponseStatus.ERROR, 
            ResponseStatusCode.INTERNAL_SERVER_ERROR, 
            "Có lỗi không xác định xảy ra"
        )


async def deleteTenant(tenant_id: UUID, current_user: User, request, db: AsyncSession):
    try:
        if not (await isCheckMaxLevel(current_user, db)):
            return api_response(
                ResponseStatus.ERROR,
                ResponseStatusCode.FORBIDDEN,
                "Chỉ quản trị viên mới có thể truy cập tài nguyên này"
        )
        if tenant_id is None:
            return api_response(
                ResponseStatus.INFO, 
                ResponseStatusCode.BAD_REQUEST, 
                "Không tồn tại tenant_id. Vui lòng kiểm tra lại đầu vào"
            )

        query_tenant_raw = select(Tenant).where(Tenant.id == tenant_id)
        query_tenant_execute = await db.execute(query_tenant_raw) 
        result_tenant = query_tenant_execute.scalar_one_or_none()

        if result_tenant is None:
            return api_response(
                ResponseStatus.INFO, 
                ResponseStatusCode.BAD_REQUEST, 
                "Không tìm thấy tenant, vui lòng kiểm tra lại"
            )

        account_map = await _get_tenant_account_map(db, result_tenant.id)
        if account_map:
            cw_res = await chatwoot_client.platform_request(
                "DELETE",
                f"/platform/api/v1/accounts/{account_map.chatwoot_id}",
            )
            if cw_res.status_code not in (200, 204, 404):
                return api_response(
                    ResponseStatus.ERROR,
                    cw_res.status_code if cw_res.status_code in (401, 404, 422, 503) else 502,
                    "Xóa account Chatwoot thất bại",
                    {
                        "chatwoot_status_code": cw_res.status_code,
                        "chatwoot_response": cw_res.data,
                    },
                )
            await db.delete(account_map)

        # Đánh dấu xóa mềm
        result_tenant.is_active = 0

        await db.commit()
        await db.refresh(result_tenant)

        return api_response(
            ResponseStatus.SUCCESS, 
            ResponseStatusCode.OK, 
            "Xóa tenant thành công"
        )
    except SQLAlchemyError as e:
        await db.rollback()
        print(f"[DB ERROR] deleteTenant: {e}")
        return api_response(
            ResponseStatus.ERROR, 
            ResponseStatusCode.INTERNAL_SERVER_ERROR, 
            "Có lỗi xảy ra khi thao tác với cơ sở dữ liệu"
        )
    except Exception as e:
        print(f"[UNEXPECTED ERROR] deleteTenant: {e}")
        return api_response(
            ResponseStatus.ERROR, 
            ResponseStatusCode.INTERNAL_SERVER_ERROR, 
            "Có lỗi không xác định xảy ra"
        )
