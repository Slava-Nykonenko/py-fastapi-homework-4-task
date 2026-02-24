import datetime

from fastapi import UploadFile
from pydantic import ConfigDict, BaseModel, field_validator

from validation import (
    validate_name,
    validate_gender,
    validate_birth_date,
    validate_image,
)


class UserProfileBaseSchema(BaseModel):
    first_name: str
    last_name: str
    gender: str
    date_of_birth: datetime.date
    info: str
    avatar: UploadFile

    model_config = ConfigDict(arbitrary_types_allowed=True)

    @field_validator("first_name", "last_name")
    def name_validator(cls, value):
        validate_name(value)
        return value.lower()

    @field_validator("gender", mode="before")
    def gender_validator(cls, value):
        validate_gender(value)
        return value

    @field_validator("date_of_birth")
    def date_of_birth_validator(cls, value):
        validate_birth_date(value)
        return value

    @field_validator("info")
    def info_validator(cls, value):
        if not value or not value.strip():
            raise ValueError(
                "Info field cannot be empty or contain only spaces."
            )
        return value

    @field_validator("avatar")
    def avatar_validator(cls, value: UploadFile):
        validate_image(value)
        return value


class UserProfileResponseSchema(BaseModel):
    id: int
    user_id: int
    first_name: str
    last_name: str
    gender: str
    date_of_birth: datetime.date
    info: str
    avatar: str

    model_config = ConfigDict(from_attributes=True)
