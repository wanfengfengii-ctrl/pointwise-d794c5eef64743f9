import sqlite3
from contextlib import contextmanager

from lake.revisions.observations import require_number


class ReceivingStore:
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
            CREATE TABLE IF NOT EXISTS accepted_handoffs (
                receipt_id INTEGER PRIMARY KEY,
                body BLOB NOT NULL
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

    def insert_accepted(self, body: bytes) -> int:
        if not self.connection.in_transaction:
            raise RuntimeError('accepting a handoff requires a receiving transaction')
        return self.connection.execute(
            'INSERT INTO accepted_handoffs (body) VALUES (?)', (body,),
        ).lastrowid

    def read_accepted(self, receipt_id: int) -> bytes:
        require_number(receipt_id, 'receipt ID')
        record = self.connection.execute(
            'SELECT body FROM accepted_handoffs WHERE receipt_id = ?', (receipt_id,),
        ).fetchone()
        if record is None:
            raise KeyError(receipt_id)
        return record[0]
