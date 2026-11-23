from pathlib import Path
from tempfile import NamedTemporaryFile

from lake.dispatch.archive import load_issued
from lake.revisions.store import SourceStore


def export_issued(store: SourceStore, handoff_id: int, destination) -> Path:
    body = load_issued(store, handoff_id)
    path = Path(destination)
    staged = None
    try:
        with NamedTemporaryFile(mode='wb', dir=path.parent, delete=False) as stream:
            staged = Path(stream.name)
            stream.write(body)
        staged.replace(path)
    finally:
        if staged is not None:
            staged.unlink(missing_ok=True)
    return path


def read_delivery(path) -> bytes:
    return Path(path).read_bytes()
