from dataclasses import dataclass

from lake.revisions.observations import (
    Observation,
    observation_order_key,
    require_day,
    require_number,
    validate_observation,
)
from lake.revisions.store import SourceStore


@dataclass(frozen=True, slots=True)
class DayRevision:
    day: str
    number: int
    parent: int | None
    observations: tuple[Observation, ...]


def load_revision(store: SourceStore, day: str, revision: int) -> DayRevision:
    require_day(day)
    require_number(revision, 'revision')
    record = store.fetch_revision(day, revision)
    if record is None:
        raise KeyError((day, revision))
    parent, observations = record
    return DayRevision(day, revision, parent, observations)


def freeze_revision(store: SourceStore, day: str, parent: int | None, observations) -> DayRevision:
    """Freeze the next day revision inside the caller's source transaction."""
    require_day(day)
    if not store.connection.in_transaction:
        raise RuntimeError('freezing a revision requires a source transaction')
    rows = tuple(observations)
    identifiers = set()
    for row in rows:
        validate_observation(row)
        if row.observation_id in identifiers:
            raise ValueError(f'duplicate observation {row.observation_id!r}')
        identifiers.add(row.observation_id)
    latest = store.latest_revision(day)
    if parent is None:
        if latest:
            raise ValueError(f'batch already exists for {day}')
    else:
        require_number(parent, 'parent revision')
        if parent > latest or store.fetch_revision(day, parent) is None:
            raise ValueError('unknown parent for this day')
    number = require_number(latest + 1, 'new revision')
    ordered = tuple(sorted(rows, key=observation_order_key))
    store.insert_revision(day, number, parent, ordered)
    return DayRevision(day, number, parent, ordered)
