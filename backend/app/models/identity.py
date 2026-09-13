from sqlalchemy import JSON, Boolean, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.common import Identified


class User(Identified, Base):
    __tablename__ = "users"
    username: Mapped[str] = mapped_column(String(80), unique=True)
    password_hash: Mapped[str] = mapped_column(Text)
    admin: Mapped[bool] = mapped_column(Boolean, default=False)


class LoginSession(Base):
    __tablename__ = "login_sessions"
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    expires_at: Mapped[float] = mapped_column(Float)


class Provider(Identified, Base):
    __tablename__ = "providers"
    kind: Mapped[str] = mapped_column(String(30), default="openai", server_default="openai")
    name: Mapped[str] = mapped_column(String(100))
    base_url: Mapped[str] = mapped_column(String(500))
    encrypted_key: Mapped[str] = mapped_column(Text, default="")
    model: Mapped[str] = mapped_column(String(200))
    context_window: Mapped[int] = mapped_column(Integer, default=32768)
    max_output_tokens: Mapped[int] = mapped_column(Integer, default=4096)
    temperature: Mapped[float] = mapped_column(Float, default=0.2)
    top_p: Mapped[float] = mapped_column(Float, default=0.9)
    timeout: Mapped[int] = mapped_column(Integer, default=180)
    max_concurrency: Mapped[int] = mapped_column(Integer, default=1)
    capabilities: Mapped[dict] = mapped_column(JSON, default=dict)
    input_cost: Mapped[float] = mapped_column(Float, default=0)
    output_cost: Mapped[float] = mapped_column(Float, default=0)
