from collections.abc import Iterable

from lake.revisions.observations import Observation
from lake.revisions.snapshots import DayRevision, freeze_revision
from lake.revisions.store import SourceStore


def intake_batch(store: SourceStore, day: str, observations: Iterable[Observation]) -> DayRevision:
    """Validate the incoming sequence and atomically freeze the day at revision 1."""
    incoming = tuple(observations)
    with store.transaction():
        revision = freeze_revision(store, day, None, incoming)
    return revision
