"""Operator-declared Context projects; attribution is never execution permission."""

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ContextProject(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    id: str = Field(min_length=1, max_length=80, pattern=r"^[A-Za-z0-9_-]+$")
    org_id: str = Field(min_length=1, max_length=128)
    name: str = Field(min_length=1, max_length=120)
    repository_id: str = Field(min_length=3, max_length=200, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*/[A-Za-z0-9][A-Za-z0-9_.-]*$")
    allowed_users: tuple[str, ...] = Field(min_length=1, max_length=1000)

    @field_validator("name", "org_id")
    @classmethod
    def printable(cls, value):
        if value != value.strip() or not value.isprintable():
            raise ValueError("invalid project label")
        return value

    @field_validator("repository_id")
    @classmethod
    def repository_slug(cls, value):
        if any(part in {".", ".."} for part in value.split("/")):
            raise ValueError("repository ID must be a slug, never a local path")
        return value

    @field_validator("allowed_users", mode="before")
    @classmethod
    def immutable_members(cls, value):
        return tuple(value) if isinstance(value, list) else value

    @field_validator("allowed_users")
    @classmethod
    def unambiguous_members(cls, value):
        if len(set(value)) != len(value) or any(not user or user != user.strip() or not user.isprintable() for user in value):
            raise ValueError("invalid project members")
        return value
