from dataclasses import dataclass

from lake.revisions.observations import require_text
from lake.revisions.store import SourceStore


@dataclass(frozen=True, slots=True)
class StationRoster:
    stations: tuple[str, ...]


def initialize_station_roster(store: SourceStore, stations=()) -> None:
    if isinstance(stations, (str, bytes)):
        raise ValueError('station roster requires a sequence of station codes')
    codes = tuple(require_text(station, 'registered station') for station in stations)
    with store.transaction():
        store.connection.execute('''
            CREATE TABLE IF NOT EXISTS station_roster (station TEXT PRIMARY KEY NOT NULL)
        ''')
        store.connection.executemany(
            'INSERT OR IGNORE INTO station_roster (station) VALUES (?)',
            ((code,) for code in codes),
        )


def load_station_roster(store: SourceStore) -> StationRoster:
    stations = store.connection.execute('SELECT station FROM station_roster')
    return StationRoster(tuple(sorted(row[0] for row in stations)))
