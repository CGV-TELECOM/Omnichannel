from sqlalchemy.ext.asyncio import AsyncSession
from app.schemas.responses.api_response_rule import api_response, ResponseStatus, ResponseStatusCode
from app.db.models import Group, User, Department, GroupUser, Levels, Tenant, Role
from sqlalchemy import select, func, or_, and_, cast, Integer, exists, delete, update, literal
from sqlalchemy.exc import SQLAlchemyError
from app.schemas.requests.group import GroupCreate, GroupUpdate
from app.utils.helpers import is_platform_admin, isCheckMaxLevelTenant
from collections import defaultdict
from uuid import UUID

async def get_groups(
    db: AsyncSession,
    current_user: User,
    id: UUID | None = None,
    page: int = 1,
    page_size: int = 10,
    search: str | None = None,
    sort_by: str | None = None,
    sort_order: str = "asc",
    department_id: UUID | None = None
):
    try:
        if id:
            return await get_group_by_id(id, db, current_user)

        # Check quyền super admin
        max_level_user = await is_platform_admin(current_user, db)

        # Check tenant active
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

        tenant_max_level_user = await isCheckMaxLevelTenant(current_user, db)

        # Query base
        base_query = (
            select(
                Group,
                Department,
                func.count(GroupUser.user_id).label("member_count")
            )
            .join(Department, Group.department_id == Department.id)
            .outerjoin(GroupUser, GroupUser.group_id == Group.id)
            .group_by(Group.id, Department.id)
        )


        # Filter quyền
        if not max_level_user:
            base_query = base_query.where(
                Group.tenant_id == current_user.tenant_id,
                Group.is_active == 1,
                or_(
                    tenant_max_level_user, # type:ignore
                    exists().where(
                        and_(
                            GroupUser.group_id == Group.id,
                            GroupUser.user_id == current_user.id
                        )
                    )
                )
            )

        # Filter search
        if search:
            like_search = f"%{search}%"
            base_query = base_query.where(
                or_(
                    Group.name.ilike(like_search),
                    Group.description.ilike(like_search)
                )
            )

        # Filter department
        if department_id:
            base_query = base_query.where(Group.department_id == department_id)

        # Sort
        if sort_by and hasattr(Group, sort_by):
            sort_col = getattr(Group, sort_by)
            base_query = base_query.order_by(
                sort_col.desc() if sort_order.lower() == "desc" else sort_col.asc()
            )

        # Count total
        count_query = select(func.count()).select_from(base_query.subquery())
        total_count = await db.scalar(count_query) or 0
        total_pages = (total_count + page_size - 1) // page_size

        # Pagination
        base_query = base_query.offset((page - 1) * page_size).limit(page_size)

        # Execute
        results = await db.execute(base_query)
        rows = results.all()

        # Format data
        group_list = [
            {
                "id": group.id,
                "name": group.name,
                "description": group.description,
                "department_id": dept.id,
                "department_name": dept.name,
                "member_count": member_count

            }
            for group, dept, member_count in rows
        ]

        return api_response(
            status=ResponseStatus.SUCCESS,
            status_code=ResponseStatusCode.OK,
            message="Lấy danh sách nhóm thành công",
            data={
                "groups": group_list,
                "total_pages": total_pages,
                "total_records": total_count
            }
        )

    except SQLAlchemyError as e:
        print(f"Database error: {str(e)}")
        return api_response(
            status=ResponseStatus.ERROR,
            status_code=ResponseStatusCode.INTERNAL_SERVER_ERROR,
            message="Lỗi khi truy vấn cơ sở dữ liệu"
        )
    except Exception as e:
        print(f"Unexpected error: {str(e)}")
        return api_response(
            status=ResponseStatus.ERROR,
            status_code=ResponseStatusCode.INTERNAL_SERVER_ERROR,
            message="Lỗi không xác định"
        )


async def check_group_access(db: AsyncSession, current_user: User, group_id: UUID) -> bool:
    try:
        # Lấy quyền super admin / tenant admin
        user_max_level = await is_platform_admin(current_user, db)
        tenant_max_level = await isCheckMaxLevelTenant(current_user, db)

        # Truy vấn duy nhất
        stmt = (
            select(literal(True))
            .select_from(Group)
            .outerjoin(GroupUser, Group.id == GroupUser.group_id)
            .where(
                Group.id == group_id,
                or_(
                    # Super admin → truy cập mọi group
                    user_max_level, # type:ignore 
                    # Tenant admin hoặc user cùng tenant
                    and_(
                        Group.tenant_id == current_user.tenant_id,
                        or_(
                            tenant_max_level, # type:ignore 
                            GroupUser.user_id == current_user.id
                        )
                    )
                ),
                *( [Group.is_active == 1] if not user_max_level else [] )
            )
            .limit(1)  # chỉ cần kiểm tra tồn tại là True
        )

        return await db.scalar(stmt) is not None

    except Exception:
        return False

async def get_group_by_id(group_id: UUID, db: AsyncSession, current_user: User):
    try:
        # Kiểm tra quyền truy cập
        if not await check_group_access(db, current_user, group_id):
            return api_response(
                status=ResponseStatus.ERROR,
                status_code=ResponseStatusCode.FORBIDDEN,
                message="Bạn không có quyền truy cập nhóm này"
            )

        # Truy vấn gộp: lấy group, department và users
        stmt = (
            select(Group, Department, User)
            .join(Department, Group.department_id == Department.id)
            .join(GroupUser, GroupUser.group_id == Group.id, isouter=True)
            .join(User, User.id == GroupUser.user_id, isouter=True)
            .where(Group.id == group_id)
        )

        result = await db.execute(stmt)
        rows = result.all()

        if not rows:
            return api_response(
                status=ResponseStatus.ERROR,
                status_code=ResponseStatusCode.NOT_FOUND,
                message="Nhóm không tồn tại"
            )

        # Lấy group + department từ dòng đầu
        group, department, _ = rows[0]

        # Gom member list
        members = [
            {
                "id": user.id,
                "fullname": user.fullname,
                "email": user.email,
                "meta_data": user.meta_data,
            }
            for _, _, user in rows if user is not None
        ]

        # Chuẩn hóa dữ liệu phản hồi
        group_data = {
            "id": group.id,
            "name": group.name,
            "description": group.description,
            "department": {
                "name": department.name,
                "description": department.description
            },
            "members": members
        }

        return api_response(
            status=ResponseStatus.SUCCESS,
            status_code=ResponseStatusCode.OK,
            message="Lấy thông tin nhóm thành công",
            data=group_data
        )

    except SQLAlchemyError as e:
        print(f"Database error: {str(e)}")
        return api_response(
            status=ResponseStatus.ERROR,
            status_code=ResponseStatusCode.INTERNAL_SERVER_ERROR,
            message="Lỗi khi truy vấn cơ sở dữ liệu"
        )

    except Exception as e:
        print(f"Unexpected error: {str(e)}")
        return api_response(
            status=ResponseStatus.ERROR,
            status_code=ResponseStatusCode.INTERNAL_SERVER_ERROR,
            message="Lỗi không xác định"
        )
    
async def create_group(group_data: GroupCreate, db: AsyncSession, current_user: User):
    try:
        user_max_level = await is_platform_admin(current_user, db)

        # Xác định tenant_id hợp lệ
        tenant_id = (group_data.tenant_id if user_max_level else None) or current_user.tenant_id

        # Kiểm tra phòng ban tồn tại
        department = await db.get(Department, group_data.department_id)
        if (
            not department
            or department.tenant_id != tenant_id  # type: ignore
            or (not user_max_level and getattr(department, "is_active", 0) != 1)
        ):
            return api_response(
                status=ResponseStatus.ERROR,
                status_code=ResponseStatusCode.NOT_FOUND,
                message="Phòng ban không tồn tại"
            )

        # Kiểm tra group trùng
        existing_group = (
            await db.execute(
                select(Group).where(
                    Group.name == group_data.name,
                    Group.tenant_id == tenant_id
                )
            )
        ).scalar_one_or_none()

        if existing_group:
            if existing_group.is_active == 1: # type:ignore
                return api_response(
                    status=ResponseStatus.ERROR,
                    status_code=ResponseStatusCode.CONFLICT,
                    message="Nhóm đã tồn tại trong phòng ban này"
                )

            # Nhóm tồn tại nhưng inactive → cập nhật lại
            await db.execute(
                update(Group)
                .where(Group.id == existing_group.id)
                .values(
                    description=group_data.description,
                    department_id=group_data.department_id,
                    is_active=1,
                    tenant_id=tenant_id
                )
            )
            group_id = existing_group.id
        else:
            # Tạo nhóm mới
            new_group = Group(
                name=group_data.name,
                description=group_data.description,
                department_id=group_data.department_id,
                tenant_id=tenant_id
            )
            db.add(new_group)
            await db.flush()  # lấy ID ngay mà chưa commit
            group_id = new_group.id

        # Thêm user hiện tại vào group
        db.add(
            GroupUser(
                group_id=group_id,
                user_id=current_user.id,
                tenant_id=tenant_id
            )
        )

        await db.commit()

        return api_response(
            status=ResponseStatus.SUCCESS,
            status_code=ResponseStatusCode.CREATED,
            message="Tạo nhóm thành công",
            data={
                "id": group_id,
                "name": group_data.name,
                "description": group_data.description,
                "department_id": group_data.department_id,
                "department_name": department.name
            }
        )

    except SQLAlchemyError as e:
        await db.rollback()
        print(f"Database error: {e}")
        return api_response(
            status=ResponseStatus.ERROR,
            status_code=ResponseStatusCode.INTERNAL_SERVER_ERROR,
            message="Lỗi khi truy vấn cơ sở dữ liệu"
        )
    except Exception as e:
        await db.rollback()
        print(f"Unexpected error: {e}")
        return api_response(
            status=ResponseStatus.ERROR,
            status_code=ResponseStatusCode.INTERNAL_SERVER_ERROR,
            message="Lỗi không xác định"
        )

async def update_group(
    group_id: UUID,
    group_data: GroupUpdate,
    db: AsyncSession,
    current_user: User
):
    try:
        user_max_level = await is_platform_admin(current_user, db)

        # Kiểm tra quyền truy cập nhóm
        if not await check_group_access(db, current_user, group_id):
            return api_response(
                status=ResponseStatus.ERROR,
                status_code=ResponseStatusCode.FORBIDDEN,
                message="Nhóm không tồn tại hoặc bạn không có quyền cập nhật nhóm này"
            )

        # Lấy nhóm cần cập nhật
        stmt = select(Group).where(Group.id == group_id)
        if not user_max_level:
            stmt = stmt.where(Group.is_active == 1)

        group = (await db.execute(stmt)).scalar_one_or_none()
        if not group:
            return api_response(
                status=ResponseStatus.ERROR,
                status_code=ResponseStatusCode.NOT_FOUND,
                message="Nhóm không tồn tại"
            )

        # Kiểm tra phòng ban hợp lệ
        department = await db.get(Department, group_data.department_id)
        if not department:
            return api_response(
                status=ResponseStatus.ERROR,
                status_code=ResponseStatusCode.NOT_FOUND,
                message="Phòng ban không tồn tại"
            )

        # Nếu không phải super admin → phải cùng tenant
        if not user_max_level and department.tenant_id != current_user.tenant_id: # type:ignore
            return api_response(
                status=ResponseStatus.ERROR,
                status_code=ResponseStatusCode.NOT_FOUND,
                message="Phòng ban không tồn tại trong tenant"
            )

        # Kiểm tra trùng tên trong cùng tenant (nếu tên thay đổi)
        if group_data.name and group_data.name != group.name:
            existing_group = (await db.execute(
                select(Group).where(
                    and_(
                        Group.name == group_data.name,
                        Group.tenant_id == group.tenant_id,
                        Group.id != group.id,
                        Group.is_active == 1
                    )
                )
            )).scalar_one_or_none()

            if existing_group:
                return api_response(
                    status=ResponseStatus.ERROR,
                    status_code=ResponseStatusCode.CONFLICT,
                    message="Tên nhóm đã tồn tại"
                )

        # Cập nhật dữ liệu nhóm
        for key, value in group_data.model_dump(exclude_unset=True).items():
            setattr(group, key, value)

        await db.commit()
        await db.refresh(group)

        return api_response(
            status=ResponseStatus.SUCCESS,
            status_code=ResponseStatusCode.OK,
            message="Cập nhật nhóm thành công",
            data={
                "id": group.id,
                "name": group.name,
                "description": group.description,
                "department_id": group.department_id,
                "department_name": department.name
            }
        )

    except SQLAlchemyError as e:
        await db.rollback()
        print(f"Database error: {e}")
        return api_response(
            status=ResponseStatus.ERROR,
            status_code=ResponseStatusCode.INTERNAL_SERVER_ERROR,
            message="Lỗi khi truy vấn cơ sở dữ liệu"
        )
    except Exception as e:
        await db.rollback()
        print(f"Unexpected error: {e}")
        return api_response(
            status=ResponseStatus.ERROR,
            status_code=ResponseStatusCode.INTERNAL_SERVER_ERROR,
            message="Lỗi không xác định"
        )

async def delete_group(group_id: UUID, db: AsyncSession, current_user: User):
    try:
        user_max_level = await is_platform_admin(current_user, db)
        # Kiểm tra quyền truy cập
        if not await check_group_access(db, current_user, group_id):
            return api_response(
                status=ResponseStatus.ERROR,
                status_code=ResponseStatusCode.FORBIDDEN,
                message="Bạn không có quyền xóa nhóm này"
            )
        stmt = select(Group).where(Group.id == group_id)
        if not user_max_level:
            # Chỉ cho phép xóa nhóm đang hoạt động
            stmt = select(Group).where(
                Group.id == group_id,
                Group.is_active == 1
            )
        # Lấy thông tin group
        result = await db.execute(stmt)
        group = result.scalar_one_or_none()

        if not group:
            return api_response(
                status=ResponseStatus.ERROR,
                status_code=ResponseStatusCode.NOT_FOUND,
                message="Nhóm không tồn tại"
            )

        # Xóa tất cả GroupUser liên quan
        await db.execute(
            delete(GroupUser).where(GroupUser.group_id == group_id)
        )

        # Xóa group
        await db.execute(
            update(Group)
            .where(Group.id == group_id)
            .values(is_active=0)
        )
        await db.commit()

        return api_response(
            status=ResponseStatus.SUCCESS,
            status_code=ResponseStatusCode.OK,
            message="Xóa nhóm thành công"
        )

    except SQLAlchemyError as e:
        await db.rollback()
        return api_response(
            status=ResponseStatus.ERROR,
            status_code=ResponseStatusCode.INTERNAL_SERVER_ERROR,
            message="Lỗi khi truy vấn cơ sở dữ liệu"
        )
    except Exception as e:
        await db.rollback()
        return api_response(
            status=ResponseStatus.ERROR,
            status_code=ResponseStatusCode.INTERNAL_SERVER_ERROR,
            message="Lỗi không xác định"
        )

async def get_group_detail(group_id: UUID, db: AsyncSession, current_user: User):
    try:        
        # Kiểm tra quyền truy cập
        if not await check_group_access(db, current_user, group_id):
            return api_response(
                status=ResponseStatus.ERROR,
                status_code=ResponseStatusCode.FORBIDDEN,
                message="Bạn không có quyền truy cập nhóm này"
        )
        
        max_level_subquery = select(func.max(Levels.level_order))
        result = await db.execute(max_level_subquery)
        max_level = result.scalar_one()
        
        # lay level cua current user
        stmt = select(Levels).where(Levels.id == current_user.level_id)
        result = await db.execute(stmt)
        level = result.scalar_one_or_none()
        
        if not level:
            return api_response(
                status=ResponseStatus.ERROR,
                status_code=ResponseStatusCode.NOT_FOUND,
                message="Không tìm thấy level của user"
            )
     
        level_order = level.level_order
        max_level = int(max_level)

        # Lấy thông tin group và department
        stmt = select(Group, Department).join(
            Department, Group.department_id == Department.id
        ).where(Group.id == group_id)
        result = await db.execute(stmt)
        group_data = result.first()

        if not group_data:
            return api_response(
                status=ResponseStatus.ERROR,
                status_code=ResponseStatusCode.NOT_FOUND,
                message="Nhóm không tồn tại"
            )

        group, department = group_data
        if level_order == max_level: # type: ignore[reportGeneralTypeIssues]
            # Lấy danh sách user trong group
            user_stmt = select(User).join(
                GroupUser, User.id == GroupUser.user_id
            ).where(GroupUser.group_id == group_id)
            user_result = await db.execute(user_stmt)
            users = user_result.scalars().all()

            # Format response data
            group_detail = {
                "id": group.id,
                "name": group.name,
                "description": group.description,
                "department": {
                    "id": department.id,
                    "name": department.name,
                    "description": department.description
                },
                "users": [
                    {
                        "id": user.id,
                        "username": user.username,
                        "email": user.email,
                        "full_name": user.fullname,
                        "meta_data": user.meta_data,
                    }
                    for user in users
                ]
            }
            return api_response(
                status=ResponseStatus.SUCCESS,
                status_code=ResponseStatusCode.OK,
                message="Lấy thông tin chi tiết nhóm thành công",
                data=group_detail
            )
        else:
            # chi tra ve nhung nguoi dung co level nho hon
            user_stmt = select(User).join(Levels, User.level_id == Levels.id).join(GroupUser, User.id == GroupUser.user_id).where(and_(GroupUser.group_id == group_id, Levels.level_order < level_order))
            user_result = await db.execute(user_stmt)
            users = user_result.scalars().all()
            group_detail = {
                "id": group.id,
                "name": group.name,
                "description": group.description,
                "department": {
                    "id": department.id,
                    "name": department.name,
                    "description": department.description
                },
                "users": [
                    {
                        "id": user.id,
                        "username": user.username,
                        "email": user.email,
                        "full_name": user.fullname,
                        "meta_data": user.meta_data,
                    }
                    for user in users
                ]
            }
            return api_response(
                status=ResponseStatus.SUCCESS,
                status_code=ResponseStatusCode.OK,
                message="Lấy thông tin chi tiết nhóm thành công",
                data=group_detail
            )

    except SQLAlchemyError as e:
        return api_response(
            status=ResponseStatus.ERROR,
            status_code=ResponseStatusCode.INTERNAL_SERVER_ERROR,
            message="Lỗi khi truy vấn cơ sở dữ liệu"
        )
    except Exception as e:
        return api_response(
            status=ResponseStatus.ERROR,
            status_code=ResponseStatusCode.INTERNAL_SERVER_ERROR,
            message="Lỗi không xác định"
        )
