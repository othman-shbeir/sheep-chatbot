"""Database bootstrap helpers for schema initialization and seed data."""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.config import Config
from app.core.logging_config import get_logger

from .database import SessionLocal, engine, init_db
from .models import User

logger = get_logger("system", __name__)


def _parse_default_user_id() -> uuid.UUID:
    raw_user_id = (Config.DEFAULT_USER_ID or "").strip()
    if not raw_user_id:
        raise ValueError("DEFAULT_USER_ID is not set.")

    try:
        return uuid.UUID(raw_user_id)
    except ValueError as exc:
        raise ValueError("DEFAULT_USER_ID must be a valid UUID.") from exc


def _parse_default_user_email() -> str:
    email = (Config.DEFAULT_USER_EMAIL or "").strip()
    if not email:
        raise ValueError("DEFAULT_USER_EMAIL is not set.")
    return email


def ensure_default_user() -> User:
    """Create or update the configured default user."""
    default_user_id = _parse_default_user_id()
    default_user_email = _parse_default_user_email()
    default_user_name = (Config.DEFAULT_USER_NAME or "").strip() or None

    with SessionLocal() as session:
        user = session.execute(
            select(User).where(User.id == default_user_id)
        ).scalar_one_or_none()

        if user is None:
            conflicting_user = session.execute(
                select(User).where(User.email == default_user_email)
            ).scalar_one_or_none()
            if conflicting_user is not None and conflicting_user.id != default_user_id:
                raise ValueError(
                    "DEFAULT_USER_EMAIL is already used by a different user ID."
                )

            user = User(
                id=default_user_id,
                email=default_user_email,
                name=default_user_name,
            )
            session.add(user)

            try:
                session.commit()
            except IntegrityError:
                session.rollback()
                user = session.execute(
                    select(User).where(User.id == default_user_id)
                ).scalar_one_or_none()
                if user is None:
                    user = session.execute(
                        select(User).where(User.email == default_user_email)
                    ).scalar_one_or_none()
                if user is None:
                    raise

                logger.info(
                    "Default user already exists after concurrent bootstrap user_id=%s email=%s",
                    user.id,
                    user.email,
                )
                return user

            logger.info(
                "Default user created user_id=%s email=%s",
                user.id,
                user.email,
            )
            return user

        updated_fields: list[str] = []
        if user.email != default_user_email:
            user.email = default_user_email
            updated_fields.append("email")
        if user.name != default_user_name:
            user.name = default_user_name
            updated_fields.append("name")

        if updated_fields:
            session.commit()
            logger.info(
                "Default user updated user_id=%s fields=%s",
                user.id,
                ",".join(updated_fields),
            )
            return user

        logger.info(
            "Default user already present user_id=%s email=%s",
            user.id,
            user.email,
        )
        return user


def bootstrap_database() -> User:
    """Create schema objects and ensure the default user exists."""
    if engine is None:
        raise ValueError("DATABASE_URL is not set in the environment.")

    logger.info("Database bootstrap started")
    init_db()
    logger.info("Database schema ensured")
    user = ensure_default_user()
    logger.info("Database bootstrap completed default_user_id=%s", user.id)
    return user


def main() -> None:
    """CLI entry point for one-time database bootstrap work."""
    bootstrap_database()


if __name__ == "__main__":
    main()
