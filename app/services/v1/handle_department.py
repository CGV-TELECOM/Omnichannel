from sqlalchemy.ext.asyncio import AsyncSession
from app.schemas.responses.api_response_rule import api_response, ResponseStatus, ResponseStatusCode
from app.db.models import Department, User, Group, GroupUser, Levels, Tenant
from sqlalchemy import select, func, or_, delete, and_, exists, update
from sqlalchemy.sql import over
from sqlalchemy.exc import SQLAlchemyError
from app.schemas.requests.department import DepartmentCreate, DepartmentUpdate
from app.utils.helpers import is_platform_admin, isCheckMaxLevelTenant
from uuid import UUID

async def get_department_by_id(department_id: UUID, db: AsyncSession, current_user: User):
    try:
        # Lấy người dùng có quyền cao nhất
        user_max_level = await is_platform_admin(current_user, db)

        # Lấy thông tin department
        stmt = select(Department).where(
            Department.id == department_id
        )
        if not user_max_level:
            stmt = select(Department).where(
                Department.id == department_id,
                Department.tenant_id == current_user.tenant_id,
                Department.is_active == 1
            )

        result = await db.execute(stmt)
        department = result.scalar_one_or_none()

        if not department:
            return api_response(
                status=ResponseStatus.ERROR,
                status_code=ResponseStatusCode.NOT_FOUND,
                message="Phòng ban không tồn tại"
            )
        
        department_data = {
            "id": department.id,
            "name": department.name,
            "description": department.description,
            "tenant_id": department.tenant_id
        }

        return api_response(
            status=ResponseStatus.SUCCESS,
            status_code=ResponseStatusCode.OK,
            message="Lấy thông tin phòng ban thành công",
            data=department_data
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


from sqlalchemy import func, select
from sqlalchemy.sql import over

async def get_departments(
    db: AsyncSession,
    current_user: User,
    id: UUID | None = None,
    page: int = 1,
    page_size: int = 10,
    search: str | None = None,
    sort_by: str | None = None,
    sort_order: str = "asc",
):
    try:
        # Nếu tìm theo ID thì gọi hàm chuyên biệt
        if id:
            return await get_department_by_id(id, db, current_user)

        # Kiểm tra quyền
        has_max_level = await is_platform_admin(current_user, db)

        # Nếu không phải max level → kiểm tra tenant
        if not has_max_level:
            if not current_user.tenant_id: # type:ignore
                return api_response(
                    status=ResponseStatus.ERROR,
                    status_code=ResponseStatusCode.NOT_FOUND,
                    message="Tenant không tồn tại"
                )

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

        # ---- Base Query ----
        count_col = func.count(Department.id).over().label("total_count")
        query = select(Department, count_col)

        if not has_max_level:
            query = query.where(
                Department.tenant_id == current_user.tenant_id,
                Department.is_active == 1
            )

        # Thêm tìm kiếm
        if search:
            query = query.where(Department.name.ilike(f"%{search}%"))

        # Thêm sắp xếp
        if sort_by and hasattr(Department, sort_by):
            sort_col = getattr(Department, sort_by)
            query = query.order_by(
                sort_col.desc() if sort_order.lower() == "desc" else sort_col.asc()
            )

        # Phân trang
        query = query.offset((page - 1) * page_size).limit(page_size)
        result = await db.execute(query)
        rows = result.all()

        if not rows:
            return api_response(
                status=ResponseStatus.SUCCESS,
                status_code=ResponseStatusCode.OK,
                message="Không có phòng ban nào",
                data={"departments": [], "total_pages": 0, "total_records": 0}
            )

        # Lấy tổng số bản ghi từ cột count
        total_count = rows[0].total_count
        total_pages = (total_count + page_size - 1) // page_size

        # Format dữ liệu trả về
        department_list = [
            {
                "id": r.Department.id,
                "name": r.Department.name,
                "description": r.Department.description,
                "is_active": r.Department.is_active
            }
            for r in rows
        ]

        return api_response(
            status=ResponseStatus.SUCCESS,
            status_code=ResponseStatusCode.OK,
            message="Lấy danh sách phòng ban thành công",
            data={
                "departments": department_list,
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


async def create_department(department_data: DepartmentCreate, db: AsyncSession, current_user: User):
    try:
        user_max_level = await is_platform_admin(current_user, db)

        # Xác định tenant_id
        if user_max_level:
            tenant_id = department_data.tenant_id or current_user.tenant_id
        else:
            tenant_id = current_user.tenant_id

        tenant = await db.scalar(
            select(Tenant).where(
                Tenant.id == tenant_id,
                Tenant.is_active == 1
            )
        )
        if not tenant:
            return api_response(
                status=ResponseStatus.ERROR,
                status_code=ResponseStatusCode.NOT_FOUND,
                message="Quyền truy cập bị vô hiệu hóa"
            )

        # 2. Tìm phòng ban đã tồn tại trong tenant
        existing_department = await db.scalar(
            select(Department).where(
                and_(
                    Department.name == department_data.name,
                    Department.tenant_id == tenant_id
                )
            )
        )
        if existing_department:
            if existing_department.is_active == 1:  # type: ignore
                return api_response(
                    status=ResponseStatus.ERROR,
                    status_code=ResponseStatusCode.CONFLICT,
                    message="Phòng ban đã tồn tại"
                )

            # Update nếu phòng ban đã tồn tại nhưng inactive
            await db.execute(
                update(Department)
                .where(Department.id == existing_department.id)
                .values(
                    description=department_data.description,
                    is_active=1
                )
            )
            await db.commit()

            return api_response(
                status=ResponseStatus.SUCCESS,
                status_code=ResponseStatusCode.CREATED,
                message="Tạo phòng ban thành công",
                data={
                    "id": existing_department.id,
                    "name": existing_department.name,
                    "description": department_data.description,
                }
            )

        # 3. Tạo mới
        new_department = Department(
            name=department_data.name,
            description=department_data.description,
            tenant_id=tenant_id,
            is_active=1
        )
        db.add(new_department)
        await db.commit()
        await db.refresh(new_department)

        return api_response(
            status=ResponseStatus.SUCCESS,
            status_code=ResponseStatusCode.CREATED,
            message="Tạo phòng ban thành công",
            data={
                "id": new_department.id,
                "name": new_department.name,
                "description": new_department.description,
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

async def update_department(department_id: UUID, department_data: DepartmentUpdate, db: AsyncSession, current_user: User):
    try:
        user_max_level = await is_platform_admin(current_user, db)

        # Xác định tenant_id
        if user_max_level:
            tenant_id = department_data.tenant_id or current_user.tenant_id
        else:
            tenant_id = current_user.tenant_id

        # 1. Kiểm tra tenant
        tenant = await db.scalar(
            select(Tenant).where(
                Tenant.id == tenant_id,
                Tenant.is_active == 1
            )
        )
        if not tenant:
            return api_response(
                status=ResponseStatus.ERROR,
                status_code=ResponseStatusCode.NOT_FOUND,
                message="Tenant không tồn tại hoặc đã bị vô hiệu hóa"
            )

        # 2. Lấy phòng ban
        department = await db.scalar(
            select(Department).where(
                Department.id == department_id,
                or_(
                    user_max_level, # type: ignore
                    Department.is_active == 1  # Nếu không max level thì phải active
                )
            )
        )
        if not department:
            return api_response(
                status=ResponseStatus.ERROR,
                status_code=ResponseStatusCode.NOT_FOUND,
                message="Phòng ban không tồn tại"
            )

        # 3. Kiểm tra quyền
        if not user_max_level and department.tenant_id != tenant_id: # type:ignore
            return api_response(
                status=ResponseStatus.ERROR,
                status_code=ResponseStatusCode.NOT_FOUND,
                message="Phòng ban không tồn tại"
            )

        # 4. Check tên trùng
        if department_data.name:
            exists = await db.scalar(
                select(Department).where(
                    Department.name == department_data.name,
                    Department.tenant_id == department.tenant_id,
                    Department.id != department_id,
                    Department.is_active == 1
                )
            )
            if exists:
                return api_response(
                    status=ResponseStatus.ERROR,
                    status_code=ResponseStatusCode.CONFLICT,
                    message="Phòng ban đã tồn tại"
                )

        # 5. Update
        update_data = department_data.model_dump(exclude_unset=True)
        await db.execute(
            update(Department)
            .where(Department.id == department_id)
            .values(**update_data)
        )
        await db.commit()

        # Lấy lại thông tin mới
        await db.refresh(department)

        return api_response(
            status=ResponseStatus.SUCCESS,
            status_code=ResponseStatusCode.OK,
            message="Cập nhật phòng ban thành công",
            data={
                "id": department.id,
                "name": department.name,
                "description": department.description
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


async def delete_department(department_id: UUID, db: AsyncSession, current_user: User):
    try:
        # Check quyền và tồn tại trong 1 query
        has_max_level = await is_platform_admin(current_user, db)

        department = await db.scalar(
            select(Department).where(
                Department.id == department_id,
                Department.is_active == 1,
                *( [Department.tenant_id == current_user.tenant_id] if not has_max_level else [] )
            )
        )

        if not department:
            return api_response(
                status=ResponseStatus.ERROR,
                status_code=ResponseStatusCode.NOT_FOUND,
                message="Phòng ban không tồn tại hoặc đã bị xóa"
            )

        # Xóa tất cả GroupUser liên quan đến department (1 query)
        await db.execute(
            delete(GroupUser).where(
                GroupUser.group_id.in_(
                    select(Group.id).where(Group.department_id == department_id)
                )
            )
        )

        # Update is_active = 0 cho tất cả Group (1 query)
        await db.execute(
            update(Group)
            .where(Group.department_id == department_id)
            .values(is_active=0)
        )

        # Update is_active = 0 cho Department (1 query)
        await db.execute(
            update(Department)
            .where(Department.id == department_id)
            .values(is_active=0)
        )

        await db.commit()

        return api_response(
            status=ResponseStatus.SUCCESS,
            status_code=ResponseStatusCode.OK,
            message="Xóa phòng ban thành công"
        )

    except SQLAlchemyError as e:
        await db.rollback()
        print(f"Database error: {str(e)}")
        return api_response(
            status=ResponseStatus.ERROR,
            status_code=ResponseStatusCode.INTERNAL_SERVER_ERROR,
            message="Lỗi khi truy vấn cơ sở dữ liệu"
        )
    except Exception as e:
        await db.rollback()
        print(f"Unexpected error: {str(e)}")
        return api_response(
            status=ResponseStatus.ERROR,
            status_code=ResponseStatusCode.INTERNAL_SERVER_ERROR,
            message="Lỗi không xác định"
        )
    
async def get_department_detail(department_id: UUID, db: AsyncSession, current_user: User):
    try:
        # Lấy thông tin department
        user_level_max = await is_platform_admin(current_user, db)

        stmt = select(Department).where(Department.id == department_id)

        if not user_level_max:
            stmt = select(Department).where(
                Department.id == department_id,
                Department.tenant_id == current_user.tenant_id,
                Department.is_active == 1
            )
        result = await db.execute(stmt)
        department = result.scalar_one_or_none()

        if not department:
            return api_response(
                status=ResponseStatus.ERROR,
                status_code=ResponseStatusCode.NOT_FOUND,
                message="Phòng ban không tồn tại"
            )

        # Lấy danh sách groups trong department
        group_stmt = (
            select(
                Group,
                func.count(GroupUser.user_id).label("member_count")
            )
            .outerjoin(GroupUser, GroupUser.group_id == Group.id)
            .where(Group.department_id == department_id)
            .group_by(Group.id)
        )
        group_result = await db.execute(group_stmt)
        groups = group_result.all()

        department_detail = {
            "id": department.id,
            "name": department.name,
            "description": department.description,
            "tenant_id": department.tenant_id,
            "groups": [
                {
                    "id": group.id,
                    "name": group.name,
                    "description": group.description,
                    "is_active": group.is_active,
                    "member_count": member_count
                }
                for group, member_count in groups
            ]
        }


        return api_response(
            status=ResponseStatus.SUCCESS,
            status_code=ResponseStatusCode.OK,
            message="Lấy thông tin chi tiết phòng ban thành công",
            data=department_detail
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
