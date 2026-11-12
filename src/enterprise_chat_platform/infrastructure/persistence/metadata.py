"""Shared SQLAlchemy metadata for persistent application models."""

from sqlalchemy import MetaData
from sqlalchemy.orm import DeclarativeBase

APP_SCHEMA = "chat"

CONSTRAINT_NAMING_CONVENTION: dict[str, str] = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_N_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    """Base class for every SQLAlchemy persistence model."""

    metadata = MetaData(
        schema=APP_SCHEMA,
        naming_convention=CONSTRAINT_NAMING_CONVENTION,
    )
