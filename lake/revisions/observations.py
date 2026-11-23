from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True, slots=True)
class Observation:
    observation_id: str
    station: str
    value: int | None
    source: str


def require_text(value, label: str) -> str:
    if type(value) is not str or not value or value != value.strip() or not value.isprintable():
        raise ValueError(f'invalid {label}')
    return value


def require_number(value, label: str) -> int:
    if type(value) is not int or not 1 <= value <= 2**63 - 1:
        raise ValueError(f'invalid {label}')
    return value


def require_day(value) -> str:
    day = require_text(value, 'batch day')
    if date.fromisoformat(day).isoformat() != day:
        raise ValueError('batch day must be YYYY-MM-DD')
    return day


def require_fields(data, *fields: str) -> None:
    if type(data) is not dict or set(data) != set(fields):
        raise ValueError(f'expected fields {fields!r}')


def require_array(data, label: str) -> list:
    if type(data) is not list:
        raise ValueError(f'{label} must be an array')
    return data


def validate_observation(row: Observation) -> None:
    if not isinstance(row, Observation):
        raise ValueError('expected Observation')
    require_text(row.observation_id, 'observation ID')
    require_text(row.station, 'station')
    require_text(row.source, 'source reference')
    if row.value is not None and type(row.value) is not int:
        raise ValueError('observation value must be an integer or None')


def observation_order_key(row: Observation) -> str:
    return row.observation_id


def observation_to_data(row: Observation) -> dict:
    validate_observation(row)
    return {
        'observation_id': row.observation_id,
        'station': row.station,
        'value': None if row.value is None else format(row.value, 'x'),
        'source': row.source,
    }


def observation_from_data(data: dict) -> Observation:
    require_fields(data, 'observation_id', 'station', 'value', 'source')
    encoded = data['value']
    value = None
    if encoded is not None:
        if type(encoded) is not str:
            raise ValueError('encoded value must be hexadecimal text or null')
        value = int(encoded, 16)
        if format(value, 'x') != encoded:
            raise ValueError('noncanonical encoded observation value')
    row = Observation(data['observation_id'], data['station'], value, data['source'])
    validate_observation(row)
    return row
