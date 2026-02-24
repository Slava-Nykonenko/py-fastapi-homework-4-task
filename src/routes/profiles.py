import mimetypes
import pathlib
from datetime import date

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    status,
    Form,
    UploadFile,
    File
)
from pydantic import ValidationError

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from config import get_jwt_auth_manager, get_s3_storage_client
from database import get_db, UserModel, UserProfileModel
from exceptions import BaseSecurityError
from schemas.profiles import UserProfileResponseSchema, UserProfileBaseSchema
from security.http import get_token
from security.interfaces import JWTAuthManagerInterface
from storages.interfaces import S3StorageInterface

router = APIRouter()


async def get_current_user(
    token: str = Depends(get_token),
    auth_manager: JWTAuthManagerInterface = Depends(get_jwt_auth_manager),
    db: AsyncSession = Depends(get_db),
) -> UserModel:
    try:
        payload = auth_manager.decode_access_token(token=token)
    except BaseSecurityError as e:
        error_msg = str(e)
        if "expired" in error_msg.lower():
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Token has expired."
            )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token is not valid."
        )

    user_id = payload.get("user_id")
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token is not valid."
        )

    user_stmt = (
        select(UserModel)
        .options(joinedload(UserModel.group))
        .where(UserModel.id == user_id)
    )
    result = await db.execute(user_stmt)
    user = result.scalars().first()

    if not user or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found or not active.",
        )

    return user


@router.post(
    "/users/{user_id}/profile/",
    response_model=UserProfileResponseSchema,
    status_code=status.HTTP_201_CREATED,
)
async def create_profile(
    user_id: int,
    current_user: UserModel = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    first_name: str = Form(...),
    last_name: str = Form(...),
    gender: str = Form(...),
    date_of_birth: date = Form(...),
    info: str = Form(...),
    avatar: UploadFile = File(...),
    s3_client: S3StorageInterface = Depends(get_s3_storage_client),
):
    if (
            user_id != current_user.id
            and current_user.group.name.lower() not in ("admin", "moderator")
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You don't have permission to edit this profile.",
        )

    stmt_user = select(UserModel).where(UserModel.id == user_id)
    result = await db.execute(stmt_user)
    target_user = result.scalars().first()
    if not target_user or not target_user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found or not active.",
        )

    stmt_existing = select(UserProfileModel).where(
        UserProfileModel.user_id == user_id
    )
    result_existing = await db.execute(stmt_existing)
    existing_profile = result_existing.scalars().first()
    if existing_profile:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="User already has a profile.",
        )
    try:
        profile_data = UserProfileBaseSchema(
            first_name=first_name,
            last_name=last_name,
            gender=gender,
            date_of_birth=date_of_birth,
            info=info,
            avatar=avatar,
        )
    except (ValueError, ValidationError) as e:
        error_msg = e.errors()[0]["msg"] if hasattr(e, "errors") else str(e)
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=error_msg
        )
    await avatar.seek(0)
    extension = pathlib.Path(avatar.filename).suffix
    if not extension:
        extension = mimetypes.guess_extension(avatar.content_type) or ".jpg"
    file_name = f"avatars/{user_id}_avatar{extension}"
    try:
        avatar_content = await avatar.read()
    finally:
        await avatar.close()
    try:
        await s3_client.upload_file(
            file_name=file_name,
            file_data=avatar_content
        )
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to upload avatar. Please try again later.",
        )

    new_profile = UserProfileModel(
        user_id=user_id,
        first_name=profile_data.first_name,
        last_name=profile_data.last_name,
        gender=profile_data.gender,
        date_of_birth=profile_data.date_of_birth,
        info=profile_data.info,
        avatar=file_name,
    )
    try:
        db.add(new_profile)
        await db.commit()
        await db.refresh(new_profile)
    except SQLAlchemyError as e:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
        )
    full_avatar_url = await s3_client.get_file_url(new_profile.avatar)
    response_data = UserProfileResponseSchema.model_validate(new_profile)
    response_data.avatar = str(full_avatar_url)

    return response_data
