"""How long a passage may be: the unit of every model call (translation, review, revision…).

Every call carries the same fixed cost (instructions, glossary, book context, neighbours) whatever
the passage length: short passages spend most of their tokens on that context, long ones risk a
truncated answer. The size is chosen at import time and recorded with the volume (EPUB) or the
chapter (text formats), because an archive restore must cut the source exactly as it was.
"""

from app.config import settings

# The size every volume imported before 0.6 was cut with; archives without a recorded size use it.
DEFAULT_PASSAGE_CHARS = 3500
MIN_PASSAGE_CHARS = 500
MAX_PASSAGE_CHARS = 20000


def passage_chars(project=None, requested: int | None = None) -> int:
    """The explicit request, else the volume's own choice (`config.passage_max_chars`), else PASSAGE_MAX_CHARS."""
    configured = ((project.config or {}).get("passage_max_chars") if project is not None else None) or None
    value = requested or configured or settings().passage_max_chars
    return max(MIN_PASSAGE_CHARS, min(MAX_PASSAGE_CHARS, int(value)))
