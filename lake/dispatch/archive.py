from lake.dispatch.envelope import HandoffEnvelope, encode_envelope
from lake.revisions.observations import require_number
from lake.revisions.store import SourceStore


def initialize_dispatch_archive(store: SourceStore) -> None:
    store.connection.execute('''
        CREATE TABLE IF NOT EXISTS issued_handoffs (
            handoff_id INTEGER PRIMARY KEY,
            body BLOB NOT NULL
        )
    ''')


def next_handoff_id(store: SourceStore) -> int:
    if not store.connection.in_transaction:
        raise RuntimeError('allocating a handoff ID requires a source transaction')
    current = store.connection.execute('SELECT COALESCE(MAX(handoff_id), 0) FROM issued_handoffs').fetchone()[0]
    return require_number(current + 1, 'handoff ID')


def store_issued(store: SourceStore, envelope: HandoffEnvelope) -> None:
    if not store.connection.in_transaction:
        raise RuntimeError('archiving a handoff requires a source transaction')
    store.connection.execute(
        'INSERT INTO issued_handoffs (handoff_id, body) VALUES (?, ?)',
        (envelope.handoff_id, encode_envelope(envelope)),
    )


def load_issued(store: SourceStore, handoff_id: int) -> bytes:
    require_number(handoff_id, 'handoff ID')
    record = store.connection.execute(
        'SELECT body FROM issued_handoffs WHERE handoff_id = ?', (handoff_id,),
    ).fetchone()
    if record is None:
        raise KeyError(handoff_id)
    return record[0]
