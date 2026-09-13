import time
import uuid

from sqlalchemy import Float, String
from sqlalchemy.orm import Mapped, mapped_column


def uid() -> str:
    return str(uuid.uuid4())


class Identified:
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    created_at: Mapped[float] = mapped_column(Float, default=time.time)
