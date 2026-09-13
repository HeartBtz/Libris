from pathlib import Path

from smoke import book

for label in ("A", "B"):
    path = Path("/tmp/opencode") / f"batch-{label.lower()}.epub"
    path.write_bytes(book("Batch fixture " + label))
    print(path)
