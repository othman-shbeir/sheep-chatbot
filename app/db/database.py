from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker
from app.config import Config


DATABASE_URL = Config.DATABASE_URL

Base = declarative_base()

engine = (
    create_engine(
        DATABASE_URL,
        pool_pre_ping=True,
        future=True,
    )
    if DATABASE_URL
    else None
)

SessionLocal = sessionmaker(
    bind=engine,
    autoflush=False,
    autocommit=False,
    future=True,
)


def get_db():
    if engine is None:
        raise ValueError("DATABASE_URL is not set in the environment.")
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

def init_db():
    if engine is None:
        raise ValueError("DATABASE_URL is not set in the environment.")
    Base.metadata.create_all(bind=engine)
