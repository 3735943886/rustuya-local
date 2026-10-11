"""Atomic replacement shared by settings, converter files and the override pack."""

import os
import tempfile
from pathlib import Path


def atomic_write(path: Path, data: str | bytes) -> None:
    """Replace a file using a unique sibling temporary file; preserve it if writing fails."""
    payload = data.encode("utf-8") if isinstance(data, str) else data
    fd, name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    temporary = Path(name)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(payload)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
