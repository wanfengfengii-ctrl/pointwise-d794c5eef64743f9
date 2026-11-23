from dataclasses import dataclass
from collections.abc import Iterable

from lake.revisions.observations import Observation
from lake.revisions.snapshots import DayRevision


@dataclass(frozen=True, slots=True)
class ObservationChange:
    observation_id: str
    before: Observation | None
    after: Observation | None


def diff_observations(before: DayRevision, after: DayRevision) -> tuple[ObservationChange, ...]:
    old = {row.observation_id: row for row in before.observations}
    new = {row.observation_id: row for row in after.observations}
    return tuple(
        ObservationChange(identifier, old.get(identifier), new.get(identifier))
        for identifier in sorted(old.keys() | new.keys())
        if old.get(identifier) != new.get(identifier)
    )


def affected_stations(changes: Iterable[ObservationChange]) -> tuple[str, ...]:
    return tuple(sorted({
        row.station
        for change in changes
        for row in (change.before, change.after)
        if row is not None
    }))
