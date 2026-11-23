import json
import sqlite3
from contextlib import contextmanager

from lake.revisions.observations import observation_from_data, observation_to_data


class SourceStore:
    def __init__(self, path):
        self.connection = sqlite3.connect(path, isolation_level=None)
        self.connection.execute('PRAGMA foreign_keys = ON')

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        self.close()

    def close(self) -> None:
        self.connection.close()

    def initialize(self) -> None:
        self.connection.execute('''
            CREATE TABLE IF NOT EXISTS day_revisions (
                day TEXT NOT NULL,
                revision INTEGER NOT NULL,
                parent INTEGER,
                observations TEXT NOT NULL,
                PRIMARY KEY (day, revision),
                FOREIGN KEY (day, parent) REFERENCES day_revisions (day, revision),
                CHECK ((revision = 1 AND parent IS NULL) OR
                       (revision > 1 AND parent IS NOT NULL AND parent < revision))
            )
        ''')

    @contextmanager
    def transaction(self):
        self.connection.execute('BEGIN IMMEDIATE')
        try:
            yield
            self.connection.commit()
        except BaseException:
            self.connection.rollback()
            raise

    def latest_revision(self, day: str) -> int:
        return self.connection.execute(
            'SELECT COALESCE(MAX(revision), 0) FROM day_revisions WHERE day = ?',
            (day,),
        ).fetchone()[0]

    def fetch_revision(self, day: str, revision: int):
        record = self.connection.execute(
            'SELECT parent, observations FROM day_revisions WHERE day = ? AND revision = ?',
            (day, revision),
        ).fetchone()
        if record is None:
            return None
        parent, encoded = record
        return parent, tuple(observation_from_data(data) for data in json.loads(encoded))

    def insert_revision(self, day: str, revision: int, parent: int | None, observations) -> None:
        if not self.connection.in_transaction:
            raise RuntimeError('freezing a revision requires a source transaction')
        encoded = json.dumps([observation_to_data(row) for row in observations], ensure_ascii=False)
        self.connection.execute(
            'INSERT INTO day_revisions (day, revision, parent, observations) VALUES (?, ?, ?, ?)',
            (day, revision, parent, encoded),
        )
