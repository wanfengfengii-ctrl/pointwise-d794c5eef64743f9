import hashlib
import json
from dataclasses import dataclass

from lake.pages.changes import ObservationChange
from lake.revisions.observations import (
    observation_from_data,
    observation_to_data,
    require_array,
    require_day,
    require_fields,
    require_number,
    require_text,
    validate_observation,
)


@dataclass(frozen=True, slots=True)
class CorrectionPage:
    page_id: str
    day: str
    lower: int
    upper: int
    number: int
    changes: tuple[ObservationChange, ...]


def canonical_json(data: dict) -> bytes:
    return json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf-8')


def _unique_fields(pairs):
    data = {}
    for key, value in pairs:
        if key in data:
            raise ValueError(f'duplicate JSON field {key!r}')
        data[key] = value
    return data


def read_json(body: bytes):
    if type(body) is not bytes:
        raise ValueError('encoded document must be bytes')
    return json.loads(body.decode('utf-8'), object_pairs_hook=_unique_fields)


def validate_interval(day: str, lower: int, upper: int) -> None:
    require_day(day)
    require_number(lower, 'lower revision')
    require_number(upper, 'upper revision')
    if upper != lower + 1:
        raise ValueError('correction interval must consist of adjacent revisions')


def page_identifier(day: str, lower: int, upper: int, number: int) -> str:
    return f'{day}/{lower}-{upper}/{number}'


def pack_page(day: str, lower: int, upper: int, number: int, changes) -> CorrectionPage:
    validate_interval(day, lower, upper)
    require_number(number, 'page number')
    supplied = tuple(changes)
    if not supplied:
        raise ValueError('a correction page must contain at least one change')
    seen = set()
    for change in supplied:
        if not isinstance(change, ObservationChange):
            raise ValueError('expected ObservationChange')
        identifier = require_text(change.observation_id, 'changed observation ID')
        if identifier in seen or change.before == change.after:
            raise ValueError('duplicate or unchanged observation on correction page')
        seen.add(identifier)
        for row in (change.before, change.after):
            if row is not None:
                validate_observation(row)
                if row.observation_id != identifier:
                    raise ValueError('change ID differs from its observation')
    ordered = tuple(sorted(supplied, key=lambda change: change.observation_id))
    return CorrectionPage(page_identifier(day, lower, upper, number), day, lower, upper, number, ordered)


def page_to_data(page: CorrectionPage) -> dict:
    return {
        'page_id': page.page_id,
        'day': page.day,
        'lower': page.lower,
        'upper': page.upper,
        'number': page.number,
        'changes': [
            {
                'observation_id': change.observation_id,
                'before': None if change.before is None else observation_to_data(change.before),
                'after': None if change.after is None else observation_to_data(change.after),
            }
            for change in page.changes
        ],
    }


def page_from_data(data: dict) -> CorrectionPage:
    require_fields(data, 'page_id', 'day', 'lower', 'upper', 'number', 'changes')
    changes = []
    for entry in require_array(data['changes'], 'page changes'):
        require_fields(entry, 'observation_id', 'before', 'after')
        before = entry['before']
        after = entry['after']
        changes.append(ObservationChange(
            entry['observation_id'],
            None if before is None else observation_from_data(before),
            None if after is None else observation_from_data(after),
        ))
    page = pack_page(data['day'], data['lower'], data['upper'], data['number'], changes)
    if data['page_id'] != page.page_id or tuple(changes) != page.changes:
        raise ValueError('page identity or change ordering does not match its contents')
    return page


def encode_page(page: CorrectionPage) -> bytes:
    return canonical_json(page_to_data(page))


def decode_page(body: bytes) -> CorrectionPage:
    return page_from_data(read_json(body))


def page_fingerprint(page: CorrectionPage) -> str:
    return hashlib.sha256(encode_page(page)).hexdigest()
