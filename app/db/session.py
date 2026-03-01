"""SQLAlchemy engine and session for ORM-based operations (user/role management).

The ORM layer is only used for the internal permission tables.
All dynamic SQL execution uses cx_Oracle directly via oracle_pool.
"""

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.core.config import settings

SQLALCHEMY_DATABASE_URL = f"oracle+cx_oracle://{settings.oracle_user}:{settings.oracle_password}@{settings.oracle_dsn}"

engine = create_engine(
    SQLALCHEMY_DATABASE_URL,
    pool_size=settings.oracle_pool_min,
    max_overflow=settings.oracle_pool_max - settings.oracle_pool_min,
    pool_pre_ping=True,
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass


def get_db():
    """FastAPI dependency that yields a SQLAlchemy session."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
