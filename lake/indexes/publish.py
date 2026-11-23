import json

from lake.indexes.build import StationIndex, build_index, index_from_data, index_to_data
from lake.indexes.stations import load_station_roster
from lake.revisions.observations import require_number
from lake.revisions.snapshots import load_revision
from lake.revisions.store import SourceStore


def initialize_indexes(store: SourceStore) -> None:
    store.connection.execute('''
        CREATE TABLE IF NOT EXISTS published_indexes (
            anchor_id INTEGER PRIMARY KEY,
            body TEXT NOT NULL
        )
    ''')


def publish_index(store: SourceStore, day: str, revision: int) -> int:
    with store.transaction():
        snapshot = load_revision(store, day, revision)
        index = build_index(snapshot, load_station_roster(store))
        encoded = json.dumps(index_to_data(index), ensure_ascii=False)
        anchor_id = store.connection.execute(
            'INSERT INTO published_indexes (body) VALUES (?)', (encoded,),
        ).lastrowid
    return anchor_id


def load_index(store: SourceStore, anchor_id: int) -> StationIndex:
    require_number(anchor_id, 'anchor ID')
    record = store.connection.execute(
        'SELECT body FROM published_indexes WHERE anchor_id = ?', (anchor_id,),
    ).fetchone()
    if record is None:
        raise KeyError(anchor_id)
    return index_from_data(json.loads(record[0]))
