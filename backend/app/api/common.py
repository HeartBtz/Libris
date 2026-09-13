from sqlalchemy import inspect


def row(obj, exclude: tuple[str, ...] = ()) -> dict:
    return {c.key: getattr(obj, c.key) for c in inspect(type(obj)).columns if c.key not in exclude}
