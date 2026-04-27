# file name: app/db.py
from collections.abc import AsyncGenerator
import uuid
import os
from dotenv import load_dotenv
import ssl
from sqlalchemy import Boolean, Column, String, Text, DateTime, ForeignKey, Integer
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase, relationship
from datetime import datetime, timezone

load_dotenv()

# ✅ Get database URL from .env
DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite+aiosqlite:///./test.db")

# ✅ Fix URL format for asyncpg
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql+asyncpg://", 1)
elif DATABASE_URL.startswith("postgresql://"):
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+asyncpg://", 1)

class Base(DeclarativeBase):
    pass

class Post(Base):
    __tablename__ = "posts"
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    likes = Column(Integer, default=0)
    caption = Column(Text)
    url = Column(String, nullable=False)
    filetype = Column(String, nullable=False)
    filename = Column(String, nullable=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    username = Column(String, nullable=False)

class Comment(Base):
    __tablename__ = "comments"
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    text = Column(Text, nullable=False)
    username = Column(String, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    post_id = Column(String, ForeignKey("posts.id"))
    post = relationship("Post")

class User(Base):
    __tablename__ = "users"
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    username = Column(String, unique=True, nullable=False)
    password = Column(String, nullable=False)
    profile_pic = Column(String, nullable=True, default=None)
    is_admin = Column(Boolean, default=False)
    is_frozen = Column(Boolean, default=False)
    frozen_until = Column(DateTime, nullable=True)

class Friendship(Base):
    __tablename__ = "friendships"
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    sender = Column(String, nullable=False)
    receiver = Column(String, nullable=False)
    status = Column(String, default="pending")
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

class Message(Base):
    __tablename__ = "messages"
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    sender = Column(String, nullable=False)
    receiver = Column(String, nullable=False)
    text = Column(Text, nullable=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

class Report(Base):
    __tablename__ = "reports"
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    reporter = Column(String, nullable=False)
    reported = Column(String, nullable=False)
    reason = Column(Text, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    is_reviewed = Column(Boolean, default=False)

# ✅ SSL required for Neon - remove sslmode from URL and handle via ssl context
if "neon.tech" in DATABASE_URL:
    DATABASE_URL = DATABASE_URL.replace("?sslmode=require", "").replace("&sslmode=require", "")
    ssl_context = ssl.create_default_context()
    engine = create_async_engine(
        DATABASE_URL,
        connect_args={"ssl": ssl_context}
    )
else:
    engine = create_async_engine(DATABASE_URL)

async_session_maker = async_sessionmaker(engine, expire_on_commit=False)

async def create_db_and_tables():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

async def get_async_session() -> AsyncGenerator[AsyncSession, None]:
    async with async_session_maker() as session:
        yield session