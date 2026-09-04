from sqlalchemy.ext.asyncio import AsyncSession
from app.schemas.responses.api_response_rule import api_response, ResponseStatus, ResponseStatusCode
from app.db.models import (
    Tenant,
    TenantKgAgent,
    User,
    ChatwootLegacyMap,
    ChatwootMapResourceType,
)
from app.schemas.requests.tenant import (
    TenantCreate,
    TenantKgAgentInput,
    TenantKgAgentsReplaceBody,
    TenantOwnSettingsResponse,
    TenantOwnSettingsUpdate,
    TenantResponse,
    TenantUpdate,
)
from sqlalchemy import select, func,  or_
from uuid import UUID
from sqlalchemy.future import select
from fastapi import Request
from app.utils.helpers import is_platform_admin
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm.attributes import flag_modified
from datetime import datetime, timezone
from typing import Any, Literal

from app.integrations.chatwoot import client as chatwoot_client
from app.integrations.chatwoot.account_payload import sanitize_platform_account_payload
from app.core.config.webcall_defaults import merge_webcall_config
from app.seeds.rbac import seed_tenant_default_roles
from app.services.v1.handle_tenant_kg_agent import (
    KgAgentSyncError,
    apply_tenant_kg_agents_sync,
    ensure_graph_activation_has_kg_agents,
    kg_agent_row_to_response,
    load_kg_agents_map,
    load_tenant_kg_agents,
    validate_tenant_kg_agent_ids,
)

# meta_data chỉ dùng nội bộ OmniHub — không đồng bộ Platform API khi chỉ sửa các key này
_CHATWOOT_META_SYNC_KEYS = frozenset(
    {
        "locale",
        "domain",
        "support_email",
        "status",
        "features",
        "limits",
        "custom_attributes",
    }
)


def _messaging_error_status(code: int) -> int:
    """Không copy 401 messaging sang HTTP 401 (FE sẽ tưởng JWT chết)."""
    if code in (404, 422, 503):
        return code
    return 502


def _should_sync_chatwoot_on_update(updates: dict) -> bool:
    """Bật/tắt bot, graph, webcall: lưu local, không PATCH Chatwoot."""
    if "name" in updates:
        return True
    meta = updates.get("meta_data")
    if isinstance(meta, dict):
        return bool(set(meta.keys()) & _CHATWOOT_META_SYNC_KEYS)
    return False


def _should_sync_chatwoot_on_update_for_tenant(
    tenant: Tenant,
    updates: dict[str, Any],
) -> bool:
    """
    Chỉ sync Chatwoot khi field account thực sự thay đổi.
    FE thường gửi full payload, nên không thể chỉ dựa vào key xuất hiện.
    """
    if "name" in updates and updates.get("name") != tenant.name:
        return True

    meta = updates.get("meta_data")
    current_meta = tenant.meta_data if isinstance(tenant.meta_data, dict) else {}
    if isinstance(meta, dict):
        for key in _CHATWOOT_META_SYNC_KEYS:
            if key in meta and meta.get(key) != current_meta.get(key):
                return True
    return False


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


async def _tenant_to_response(db: AsyncSession, tenant: Tenant) -> TenantResponse:
    kg_rows = (
        list(tenant.kg_agents)
        if tenant.kg_agents
        else await load_tenant_kg_agents(db, tenant.id)
    )
    base = TenantResponse.model_validate(tenant, from_attributes=True)
    return base.model_copy(
        update={
            "kg_agents": [kg_agent_row_to_response(r) for r in kg_rows],
        }
    )


async def getAllTenant(_: Request, current_user: User, id: UUID | None, graph_id: UUID | None, kg_agent_id: UUID | None, is_active: int | None, graph_activated: int | None, page: int, page_size: int, search: str | None, db: AsyncSession):
    try:
        is_super_admin = await is_platform_admin(current_user, db)

        if id:
            if not is_super_admin and id != current_user.tenant_id:
                return api_response(
                    ResponseStatus.ERROR,
                    ResponseStatusCode.FORBIDDEN,
                    "Bạn chỉ có thể xem tenant của mình",
                )

            query_tenant_raw = select(Tenant).where(Tenant.id == id)
            query_tenant_execute = await db.execute(query_tenant_raw)
            result_tenant = query_tenant_execute.scalar_one_or_none()

            if result_tenant is None:
                return api_response(ResponseStatus.INFO, ResponseStatusCode.BAD_REQUEST, "Tenant không tồn tại")

            return api_response(
                ResponseStatus.SUCCESS,
                ResponseStatusCode.OK,
                "Tìm tenant theo ID thành công",
                await _tenant_to_response(db, result_tenant),
            )
        else:
            query = select(Tenant)
            if not is_super_admin:
                if current_user.tenant_id is None:
                    return api_response(
                        ResponseStatus.ERROR,
                        ResponseStatusCode.FORBIDDEN,
                        "Tài khoản chưa thuộc tenant nào",
                    )
                query = query.where(Tenant.id == current_user.tenant_id)
            if graph_id:
                query = query.where(Tenant.graph_id == graph_id)
            if kg_agent_id:
                query = query.join(
                    TenantKgAgent, TenantKgAgent.tenant_id == Tenant.id
                ).where(TenantKgAgent.kg_agent_id == kg_agent_id)
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
            tenants = result.scalars().unique().all()

            kg_map = await load_kg_agents_map(db, [t.id for t in tenants])
            tenant_list = []
            for t in tenants:
                base = TenantResponse.model_validate(t, from_attributes=True)
                rows = kg_map.get(t.id, [])
                tenant_list.append(
                    base.model_copy(
                        update={
                            "kg_agents": [
                                kg_agent_row_to_response(r) for r in rows
                            ],
                        }
                    )
                )

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
        if not (await is_platform_admin(current_user, db)):
            return api_response(
                ResponseStatus.ERROR,
                ResponseStatusCode.FORBIDDEN,
                "Chỉ platform admin mới có thể truy cập tài nguyên này",
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

        from app.services.v1.handle_chatwoot.chatbot import (
            default_tenant_bot_meta,
            normalize_messaging_bots_meta,
        )

        meta = default_tenant_bot_meta()
        if tenant_data.meta_data and isinstance(tenant_data.meta_data, dict):
            meta.update(tenant_data.meta_data)
        meta, _ = normalize_messaging_bots_meta(meta)

        # Tạo tenant mới
        new_tenant = Tenant(
            name=tenant_data.name,
            description=tenant_data.description,
            meta_data=meta,
            graph_id=tenant_data.graph_id,
            graph_activated=tenant_data.graph_activated if tenant_data.graph_activated is not None else 0,
            webcall_config=merge_webcall_config(tenant_data.webcall_config),
            conversation_rating_enabled=(
                True
                if tenant_data.conversation_rating_enabled is None
                else bool(tenant_data.conversation_rating_enabled)
            ),
        )
        db.add(new_tenant)
        await db.flush()

        if tenant_data.kg_agents is not None:
            try:
                await apply_tenant_kg_agents_sync(
                    db, new_tenant, tenant_data.kg_agents
                )
                flag_modified(new_tenant, "meta_data")
            except (ValueError, KgAgentSyncError) as ve:
                await db.rollback()
                return api_response(
                    ResponseStatus.ERROR,
                    ResponseStatusCode.BAD_REQUEST,
                    str(ve),
                )
        else:
            try:
                await ensure_graph_activation_has_kg_agents(db, new_tenant)
            except KgAgentSyncError as ve:
                await db.rollback()
                return api_response(
                    ResponseStatus.ERROR,
                    ResponseStatusCode.BAD_REQUEST,
                    str(ve),
                )

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
                _messaging_error_status(cw_res.status_code),
                "Tạo account messaging thất bại, đã rollback tạo tenant",
                {
                    "messaging_status_code": cw_res.status_code,
                    "messaging_response": cw_res.data,
                },
            )
        try:
            chatwoot_account_id = int(cw_res.data["id"])
        except (TypeError, ValueError):
            await db.rollback()
            return api_response(
                ResponseStatus.ERROR,
                502,
                "Messaging trả id account không hợp lệ, đã rollback tạo tenant",
                {"messaging_response": cw_res.data},
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
                    "Lỗi khi xóa account messaging %s sau khi liên kết thất bại: %s",
                    chatwoot_account_id,
                    str(delete_ex),
                )
            await db.rollback()
            msg = "Gắn user tích hợp vào messaging account thất bại, đã rollback tạo doanh nghiệp"
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

        # Seed role mặc định admin-partner + user cho tenant mới
        await seed_tenant_default_roles(db, new_tenant.id)

        await db.commit()
        await db.refresh(new_tenant)

        return api_response(
            ResponseStatus.SUCCESS, 
            ResponseStatusCode.CREATED, 
            "Thêm tenant thành công",
            data={
                "tenant": await _tenant_to_response(db, new_tenant),
                "messaging_linked": True,
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
        if not (await is_platform_admin(current_user, db)):
            return api_response(
                ResponseStatus.ERROR,
                ResponseStatusCode.FORBIDDEN,
                "Chỉ platform admin mới có thể truy cập tài nguyên này",
            )
        
        # 2. Kiểm tra trùng tên tenant (trừ chính tenant đang cập nhật)
        if tenant_data.name is not None:
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
        updates = tenant_data.model_dump(exclude_unset=True)
        kg_agents_payload = updates.pop("kg_agents", None)
        # Quyết định sync dựa trên giá trị thực sự thay đổi, không chỉ dựa vào key có mặt.
        sync_chatwoot = _should_sync_chatwoot_on_update_for_tenant(tenant, updates)
        if "webcall_config" in updates:
            updates["webcall_config"] = merge_webcall_config(updates["webcall_config"])
        if "meta_data" in updates and isinstance(updates["meta_data"], dict):
            existing_meta = tenant.meta_data if isinstance(tenant.meta_data, dict) else {}
            updates["meta_data"] = {**existing_meta, **updates["meta_data"]}
        for field, value in updates.items():
            setattr(tenant, field, value)

        if kg_agents_payload is not None:
            try:
                await apply_tenant_kg_agents_sync(
                    db,
                    tenant,
                    [TenantKgAgentInput.model_validate(x) for x in kg_agents_payload],
                )
                flag_modified(tenant, "meta_data")
            except (ValueError, KgAgentSyncError) as ve:
                await db.rollback()
                return api_response(
                    ResponseStatus.ERROR,
                    ResponseStatusCode.BAD_REQUEST,
                    str(ve),
                )
        elif "graph_activated" in updates:
            try:
                await ensure_graph_activation_has_kg_agents(db, tenant)
            except KgAgentSyncError as ve:
                await db.rollback()
                return api_response(
                    ResponseStatus.ERROR,
                    ResponseStatusCode.BAD_REQUEST,
                    str(ve),
                )

        account_map = await _get_tenant_account_map(db, tenant.id)
        if account_map and sync_chatwoot:
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
                    _messaging_error_status(cw_res.status_code),
                    "Cập nhật account messaging thất bại",
                    {
                        "messaging_status_code": cw_res.status_code,
                        "messaging_response": cw_res.data,
                    },
                )
        elif not account_map and sync_chatwoot:
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
                    _messaging_error_status(cw_create.status_code),
                    "Tenant chưa có map và tạo account messaging mới thất bại",
                    {
                        "messaging_status_code": cw_create.status_code,
                        "messaging_response": cw_create.data,
                    },
                )
            try:
                new_account_id = int(cw_create.data["id"])
            except (TypeError, ValueError):
                await db.rollback()
                return api_response(
                    ResponseStatus.ERROR,
                    502,
                    "Messaging trả id account không hợp lệ",
                    {"messaging_response": cw_create.data},
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
        if sync_chatwoot:
            snap, _ = _tenant_chatwoot_account_payload(tenant)
            tenant.meta_data["chatwoot_account"] = dict(snap)

        await db.commit()
        await db.refresh(tenant)

        return api_response(
            ResponseStatus.SUCCESS,
            ResponseStatusCode.OK,
            "Cập nhật tenant thành công",
            data=await _tenant_to_response(db, tenant),
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
        if not (await is_platform_admin(current_user, db)):
            return api_response(
                ResponseStatus.ERROR,
                ResponseStatusCode.FORBIDDEN,
                "Chỉ platform admin mới có thể truy cập tài nguyên này",
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
                    _messaging_error_status(cw_res.status_code),
                    "Xóa account messaging thất bại",
                    {
                        "messaging_status_code": cw_res.status_code,
                        "messaging_response": cw_res.data,
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


_DEFAULT_RESPONDER: Literal["bot", "agent"] = "agent"


def _tenant_own_settings_payload(tenant: Tenant) -> TenantOwnSettingsResponse:
    from app.services.v1.handle_chatwoot.chatbot import (
        messaging_bots_public_list,
        parse_tenant_messaging_bots,
    )

    meta = tenant.meta_data if isinstance(tenant.meta_data, dict) else {}
    responder = meta.get("default_responder", _DEFAULT_RESPONDER)
    if responder not in ("bot", "agent"):
        responder = _DEFAULT_RESPONDER
    bots = parse_tenant_messaging_bots(meta)
    return TenantOwnSettingsResponse(
        conversation_rating_enabled=bool(
            getattr(tenant, "conversation_rating_enabled", True)
        ),
        chatbot_enabled=meta.get("chatbot_enabled") is not False,
        default_responder=responder,
        messaging_bots=messaging_bots_public_list(bots),
    )


async def _ensure_messaging_bots_normalized(
    db: AsyncSession, tenant: Tenant
) -> Tenant:
    """Lazy migrate: luôn có messaging_bots (có thể []); bỏ shorthand legacy."""
    from app.services.v1.handle_chatwoot.chatbot import normalize_messaging_bots_meta

    meta = tenant.meta_data if isinstance(tenant.meta_data, dict) else {}
    new_meta, changed = normalize_messaging_bots_meta(meta)
    if not changed:
        return tenant
    tenant.meta_data = new_meta
    flag_modified(tenant, "meta_data")
    await db.commit()
    await db.refresh(tenant)
    return tenant


async def _validate_messaging_bot_agent_uuids(
    db: AsyncSession,
    tenant_id: UUID,
    agent_uuids: list[UUID],
) -> str | None:
    """None nếu OK; message lỗi nếu UUID không map được."""
    if not agent_uuids:
        return None
    from app.services.v1.handle_chatwoot._shared import (
        _translate_local_agent_uuids_to_remote,
    )

    _remote, missing = await _translate_local_agent_uuids_to_remote(
        db, tenant_id, agent_uuids
    )
    if missing:
        return (
            "Agent UUID chưa có map messaging (gọi GET agents trước): "
            + ", ".join(missing)
        )
    return None


async def _require_own_tenant(current_user: User, db: AsyncSession):
    if current_user.tenant_id is None:
        return api_response(
            ResponseStatus.ERROR,
            ResponseStatusCode.FORBIDDEN,
            "Tài khoản chưa thuộc tenant nào",
        )
    tenant = await db.get(Tenant, current_user.tenant_id)
    if tenant is None or tenant.is_active == 0:
        return api_response(
            ResponseStatus.ERROR,
            ResponseStatusCode.NOT_FOUND,
            "Không tìm thấy tenant của bạn",
        )
    return tenant


async def getOwnTenantSettings(current_user: User, db: AsyncSession):
    try:
        tenant = await _require_own_tenant(current_user, db)
        if not isinstance(tenant, Tenant):
            return tenant
        tenant = await _ensure_messaging_bots_normalized(db, tenant)
        return api_response(
            ResponseStatus.SUCCESS,
            ResponseStatusCode.OK,
            "Lấy cài đặt tenant thành công",
            data=_tenant_own_settings_payload(tenant),
        )
    except Exception as e:
        print(f"[UNEXPECTED ERROR] getOwnTenantSettings: {e}")
        return api_response(
            ResponseStatus.ERROR,
            ResponseStatusCode.INTERNAL_SERVER_ERROR,
            "Có lỗi không xác định xảy ra",
        )


async def updateOwnTenantSettings(
    current_user: User,
    settings_data: TenantOwnSettingsUpdate,
    db: AsyncSession,
):
    """
    Admin tenant cập nhật cài đặt vận hành của chính tenant mình.
    Không đổi tên / graph / webcall / Chatwoot account — không sync Platform API.
    """
    try:
        tenant = await _require_own_tenant(current_user, db)
        if not isinstance(tenant, Tenant):
            return tenant

        updates = settings_data.model_dump(exclude_unset=True)
        if not updates:
            tenant = await _ensure_messaging_bots_normalized(db, tenant)
            return api_response(
                ResponseStatus.SUCCESS,
                ResponseStatusCode.OK,
                "Không có thay đổi",
                data=_tenant_own_settings_payload(tenant),
            )

        if "conversation_rating_enabled" in updates:
            tenant.conversation_rating_enabled = bool(
                updates["conversation_rating_enabled"]
            )

        meta_keys = ("chatbot_enabled", "default_responder", "messaging_bots")
        if any(k in updates for k in meta_keys):
            from app.services.v1.handle_chatwoot.chatbot import (
                messaging_bots_to_meta_list,
                normalize_messaging_bots_meta,
            )

            meta = dict(tenant.meta_data) if isinstance(tenant.meta_data, dict) else {}
            meta, _ = normalize_messaging_bots_meta(meta)

            if "chatbot_enabled" in updates:
                meta["chatbot_enabled"] = bool(updates["chatbot_enabled"])
            if "default_responder" in updates:
                meta["default_responder"] = updates["default_responder"]

            if "messaging_bots" in updates:
                bots_raw = updates["messaging_bots"] or []
                from app.services.v1.handle_chatwoot.chatbot import (
                    merge_messaging_bots_preserving_tokens,
                )

                entries = merge_messaging_bots_preserving_tokens(
                    bots_raw, meta
                )
                uuids: list[UUID] = [e.agent_uuid for e in entries]

                err = await _validate_messaging_bot_agent_uuids(
                    db, tenant.id, uuids
                )
                if err:
                    return api_response(
                        ResponseStatus.ERROR,
                        ResponseStatusCode.BAD_REQUEST,
                        err,
                    )
                kg_row_ids = [
                    e.tenant_kg_agent_id
                    for e in entries
                    if e.tenant_kg_agent_id is not None
                ]
                kg_err = await validate_tenant_kg_agent_ids(
                    db, tenant.id, kg_row_ids
                )
                if kg_err:
                    return api_response(
                        ResponseStatus.ERROR,
                        ResponseStatusCode.BAD_REQUEST,
                        kg_err,
                    )
                if entries and not any(e.is_default for e in entries):
                    entries[0].is_default = True
                elif sum(1 for e in entries if e.is_default) > 1:
                    seen = False
                    for e in entries:
                        if e.is_default:
                            if seen:
                                e.is_default = False
                            else:
                                seen = True

                meta["messaging_bots"] = messaging_bots_to_meta_list(entries)
                meta.pop("messaging_ai_bot_agent_uuid", None)

                # Có bot + chưa set responder → bot; list rỗng → giữ responder hiện tại
                if entries and "default_responder" not in updates:
                    if meta.get("default_responder") not in ("bot", "agent"):
                        meta["default_responder"] = "bot"

            tenant.meta_data = meta
            flag_modified(tenant, "meta_data")
        else:
            await _ensure_messaging_bots_normalized(db, tenant)

        await db.commit()
        await db.refresh(tenant)

        return api_response(
            ResponseStatus.SUCCESS,
            ResponseStatusCode.OK,
            "Cập nhật cài đặt tenant thành công",
            data=_tenant_own_settings_payload(tenant),
        )
    except SQLAlchemyError as e:
        await db.rollback()
        print(f"[DB ERROR] updateOwnTenantSettings: {e}")
        return api_response(
            ResponseStatus.ERROR,
            ResponseStatusCode.INTERNAL_SERVER_ERROR,
            "Có lỗi xảy ra khi thao tác với cơ sở dữ liệu",
        )
    except Exception as e:
        await db.rollback()
        print(f"[UNEXPECTED ERROR] updateOwnTenantSettings: {e}")
        return api_response(
            ResponseStatus.ERROR,
            ResponseStatusCode.INTERNAL_SERVER_ERROR,
            "Có lỗi không xác định xảy ra",
        )


async def listTenantKgAgents(
    tenant_id: UUID,
    current_user: User,
    db: AsyncSession,
):
    try:
        if not (await is_platform_admin(current_user, db)):
            if current_user.tenant_id != tenant_id:
                return api_response(
                    ResponseStatus.ERROR,
                    ResponseStatusCode.FORBIDDEN,
                    "Bạn chỉ có thể xem KG agents của tenant mình",
                )
        tenant = await db.get(Tenant, tenant_id)
        if tenant is None or tenant.is_active == 0:
            return api_response(
                ResponseStatus.ERROR,
                ResponseStatusCode.NOT_FOUND,
                "Không tìm thấy tenant",
            )
        rows = await load_tenant_kg_agents(db, tenant_id)
        return api_response(
            ResponseStatus.SUCCESS,
            ResponseStatusCode.OK,
            "Lấy danh sách KG agents thành công",
            {
                "tenant_id": str(tenant_id),
                "kg_agents": [kg_agent_row_to_response(r).model_dump() for r in rows],
            },
        )
    except Exception as e:
        print(f"[UNEXPECTED ERROR] listTenantKgAgents: {e}")
        return api_response(
            ResponseStatus.ERROR,
            ResponseStatusCode.INTERNAL_SERVER_ERROR,
            "Có lỗi không xác định xảy ra",
        )


async def replaceTenantKgAgents(
    tenant_id: UUID,
    current_user: User,
    body: TenantKgAgentsReplaceBody,
    db: AsyncSession,
):
    try:
        if not (await is_platform_admin(current_user, db)):
            return api_response(
                ResponseStatus.ERROR,
                ResponseStatusCode.FORBIDDEN,
                "Chỉ platform admin mới có thể truy cập tài nguyên này",
            )
        tenant = await db.get(Tenant, tenant_id)
        if tenant is None or tenant.is_active == 0:
            return api_response(
                ResponseStatus.ERROR,
                ResponseStatusCode.NOT_FOUND,
                "Không tìm thấy tenant",
            )
        try:
            rows = await apply_tenant_kg_agents_sync(db, tenant, body.kg_agents)
            flag_modified(tenant, "meta_data")
        except (ValueError, KgAgentSyncError) as ve:
            return api_response(
                ResponseStatus.ERROR,
                ResponseStatusCode.BAD_REQUEST,
                str(ve),
            )
        await db.commit()
        return api_response(
            ResponseStatus.SUCCESS,
            ResponseStatusCode.OK,
            "Cập nhật KG agents thành công",
            {
                "tenant_id": str(tenant_id),
                "kg_agents": [kg_agent_row_to_response(r).model_dump() for r in rows],
            },
        )
    except SQLAlchemyError as e:
        await db.rollback()
        print(f"[DB ERROR] replaceTenantKgAgents: {e}")
        return api_response(
            ResponseStatus.ERROR,
            ResponseStatusCode.INTERNAL_SERVER_ERROR,
            "Có lỗi xảy ra khi thao tác với cơ sở dữ liệu",
        )
    except Exception as e:
        await db.rollback()
        print(f"[UNEXPECTED ERROR] replaceTenantKgAgents: {e}")
        return api_response(
            ResponseStatus.ERROR,
            ResponseStatusCode.INTERNAL_SERVER_ERROR,
            "Có lỗi không xác định xảy ra",
        )
