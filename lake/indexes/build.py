from dataclasses import dataclass

from lake.indexes.postings import StationPosting, build_postings
from lake.indexes.stations import StationRoster
from lake.revisions.observations import (
    observation_from_data,
    observation_to_data,
    require_array,
    require_day,
    require_fields,
    require_number,
    require_text,
)
from lake.revisions.snapshots import DayRevision


@dataclass(frozen=True, slots=True)
class StationIndex:
    day: str
    revision: int
    roster: StationRoster
    postings: tuple[StationPosting, ...]


def build_index(revision: DayRevision, roster: StationRoster) -> StationIndex:
    return StationIndex(
        revision.day,
        revision.number,
        roster,
        build_postings(revision.observations, roster.stations),
    )


def index_to_data(index: StationIndex) -> dict:
    return {
        'day': index.day,
        'revision': index.revision,
        'roster': list(index.roster.stations),
        'postings': [
            {'station': posting.station, 'rows': [observation_to_data(row) for row in posting.rows]}
            for posting in index.postings
        ],
    }


def index_from_data(data: dict) -> StationIndex:
    require_fields(data, 'day', 'revision', 'roster', 'postings')
    day = require_day(data['day'])
    revision = require_number(data['revision'], 'index revision')
    roster = tuple(require_text(code, 'registered station') for code in require_array(data['roster'], 'roster'))
    if roster != tuple(sorted(set(roster))):
        raise ValueError('index roster must be ordered and unique')
    postings = []
    for posting in require_array(data['postings'], 'index postings'):
        require_fields(posting, 'station', 'rows')
        station = require_text(posting['station'], 'posting station')
        rows = tuple(observation_from_data(row) for row in require_array(posting['rows'], 'posting rows'))
        postings.append(StationPosting(station, rows))
    canonical = build_postings((row for posting in postings for row in posting.rows), roster)
    if tuple(postings) != canonical:
        raise ValueError('index postings do not match station rows or roster order')
    return StationIndex(day, revision, StationRoster(roster), canonical)
