from dataclasses import dataclass

from lake.revisions.observations import Observation, observation_order_key, validate_observation


@dataclass(frozen=True, slots=True)
class StationPosting:
    station: str
    rows: tuple[Observation, ...]


def build_postings(observations, stations=()) -> tuple[StationPosting, ...]:
    by_station = {station: [] for station in stations}
    identifiers = set()
    for row in observations:
        validate_observation(row)
        if row.observation_id in identifiers:
            raise ValueError('observation appears more than once in index')
        identifiers.add(row.observation_id)
        by_station.setdefault(row.station, []).append(row)
    return tuple(
        StationPosting(station, tuple(sorted(rows, key=observation_order_key)))
        for station, rows in sorted(by_station.items())
    )


def ordered_members(posting: StationPosting) -> tuple[str, ...]:
    return tuple(row.observation_id for row in posting.rows)
