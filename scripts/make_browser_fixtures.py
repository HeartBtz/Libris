from pathlib import Path

from smoke import book

for label in ("A", "B"):
    path = Path("/tmp/libris") / f"batch-{label.lower()}.epub"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(book("Batch fixture " + label))
    print(path)
