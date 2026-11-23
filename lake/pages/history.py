from lake.pages.changes import diff_observations
from lake.pages.manifest import IntervalManifest, decode_manifest, encode_manifest, freeze_manifest
from lake.pages.pack import CorrectionPage, decode_page, encode_page, pack_page
from lake.revisions.snapshots import DayRevision, load_revision
from lake.revisions.store import SourceStore


def initialize_page_history(store: SourceStore) -> None:
    with store.transaction():
        store.connection.execute('''
            CREATE TABLE IF NOT EXISTS sealed_manifests (
                day TEXT NOT NULL,
                lower INTEGER NOT NULL,
                upper INTEGER NOT NULL,
                body BLOB NOT NULL,
                PRIMARY KEY (day, lower, upper)
            )
        ''')
        store.connection.execute('''
            CREATE TABLE IF NOT EXISTS sealed_pages (
                page_id TEXT PRIMARY KEY NOT NULL,
                day TEXT NOT NULL,
                lower INTEGER NOT NULL,
                upper INTEGER NOT NULL,
                body BLOB NOT NULL,
                FOREIGN KEY (day, lower, upper) REFERENCES sealed_manifests (day, lower, upper)
            )
        ''')


def seal_interval(store: SourceStore, before: DayRevision, after: DayRevision) -> IntervalManifest:
    if before.day != after.day or after.number != before.number + 1:
        raise ValueError('sealing requires adjacent revisions of the same day')
    day, lower, upper = before.day, before.number, after.number
    with store.transaction():
        before = load_revision(store, day, lower)
        after = load_revision(store, day, upper)
        try:
            return load_interval_manifest(store, day, lower, upper)
        except KeyError:
            pass
        changes = diff_observations(before, after)
        pages = tuple(
            pack_page(day, lower, upper, number, (change,))
            for number, change in enumerate(changes, start=1)
        )
        manifest = freeze_manifest(day, lower, upper, pages)
        store.connection.execute(
            'INSERT INTO sealed_manifests (day, lower, upper, body) VALUES (?, ?, ?, ?)',
            (day, lower, upper, encode_manifest(manifest)),
        )
        store.connection.executemany(
            'INSERT INTO sealed_pages (page_id, day, lower, upper, body) VALUES (?, ?, ?, ?, ?)',
            ((page.page_id, day, lower, upper, encode_page(page)) for page in pages),
        )
    return manifest


def load_page(store: SourceStore, page_id: str) -> CorrectionPage:
    record = store.connection.execute('SELECT body FROM sealed_pages WHERE page_id = ?', (page_id,)).fetchone()
    if record is None:
        raise KeyError(page_id)
    return decode_page(record[0])


def load_interval_manifest(store: SourceStore, day: str, lower: int, upper: int) -> IntervalManifest:
    record = store.connection.execute(
        'SELECT body FROM sealed_manifests WHERE day = ? AND lower = ? AND upper = ?',
        (day, lower, upper),
    ).fetchone()
    if record is None:
        raise KeyError((day, lower, upper))
    return decode_manifest(record[0])


def load_interval_chain(store: SourceStore, day: str, anchor: int, target: int) -> tuple[IntervalManifest, ...]:
    load_revision(store, day, anchor)
    load_revision(store, day, target)
    lower, upper = sorted((anchor, target))
    return tuple(load_interval_manifest(store, day, number, number + 1) for number in range(lower, upper))
