from sqlalchemy import (
    Column,
    Integer,
    String,
    BigInteger,
    Boolean,
    DateTime,
    Text,
    ForeignKey
)

from sqlalchemy.sql import func
from sqlalchemy.orm import relationship

from database.db import Base


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True)

    telegram_id = Column(BigInteger, unique=True, nullable=False, index=True)

    username = Column(String(100))
    first_name = Column(String(100))
    last_name = Column(String(100))

    language = Column(String(10), default="fa")

    provider = Column(String(50), default="node1")
    model = Column(String(100), default="phi3.5")

    credits = Column(Integer, default=1000)

    is_admin = Column(Boolean, default=False)
    enabled = Column(Boolean, default=True)
    chat_mode = Column(String(20), default="chat")
    tts_voice = Column(String(50), default="orion")

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())


class Message(Base):
    __tablename__ = "messages"

    id = Column(Integer, primary_key=True)

    user_id = Column(Integer, ForeignKey("users.id"))

    role = Column(String(20))
    provider = Column(String(50))
    model = Column(String(100))

    content = Column(Text)

    created_at = Column(DateTime(timezone=True), server_default=func.now())

    user = relationship("User")


class Node(Base):
    __tablename__ = "nodes"

    id = Column(Integer, primary_key=True)

    name = Column(String(50), unique=True, nullable=False)
    url = Column(String(255), nullable=False)
    status = Column(String(20), default="online")

    created_at = Column(DateTime(timezone=True), server_default=func.now())


class Provider(Base):
    __tablename__ = "providers"

    id = Column(Integer, primary_key=True)

    name = Column(String(50), unique=True, nullable=False)
    base_url = Column(String(255))
    api_key = Column(Text)

    enabled = Column(Boolean, default=True)
    chat_mode = Column(String(20), default="chat")
    tts_voice = Column(String(50), default="orion")

    created_at = Column(DateTime(timezone=True), server_default=func.now())
