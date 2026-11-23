from lake.dispatch.envelope import decode_envelope
from lake.pages.verify import verify_delivered_pages
from lake.receiving.store import ReceivingStore


def accept_handoff(store: ReceivingStore, body: bytes) -> int:
    envelope = decode_envelope(body)
    verify_delivered_pages(envelope.manifests, envelope.delivered_pages)
    with store.transaction():
        receipt_id = store.insert_accepted(body)
    return receipt_id
