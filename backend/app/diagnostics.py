import traceback
from pathlib import Path


def safe_trace(exc: BaseException, limit: int = 8) -> str:
    """Where an error happened, without its message: service logs must never contain book text."""
    chain = []
    current: BaseException | None = exc
    while current is not None and len(chain) < 4:
        chain.append(type(current).__name__)
        current = current.__cause__ or current.__context__
    frames = traceback.extract_tb(exc.__traceback__)[-limit:]
    path = " <- ".join(f"{Path(f.filename).name}:{f.lineno}:{f.name}" for f in reversed(frames))
    return f"{' <- '.join(chain)} at {path or 'unknown'}"
