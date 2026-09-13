from contextvars import ContextVar

# Inherited by async calls of one job, never global across simultaneous requests or users.
execution: ContextVar[tuple[str, str] | None] = ContextVar("job_execution", default=None)
