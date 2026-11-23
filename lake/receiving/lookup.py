from collections import defaultdict

from lake.receiving.receipts import StationReceipt, build_station_receipt, load_accepted_handoff
from lake.receiving.store import ReceivingStore
from lake.revisions.observations import require_text


def lookup_station(store: ReceivingStore, receipt_id: int, station: str) -> StationReceipt:
    require_text(station, 'queried station')
    envelope = load_accepted_handoff(store, receipt_id)
    delivered_ids = {page.page_id for page in envelope.delivered_pages}
    missing = tuple(
        entry.page_id
        for manifest in envelope.manifests
        for entry in manifest.entries
        if station in entry.affected_stations and entry.page_id not in delivered_ids
    )
    if missing:
        return build_station_receipt(receipt_id, envelope, station, None, missing)

    members = {
        row.observation_id: row
        for posting in envelope.anchor.postings
        if posting.station == station
        for row in posting.rows
    }
    pages_by_interval = defaultdict(list)
    for page in envelope.delivered_pages:
        pages_by_interval[page.lower].append(page)

    forward = envelope.anchor.revision <= envelope.target_revision
    intervals = envelope.manifests if forward else reversed(envelope.manifests)
    # A station with complete evidence has every transition that can change its local rows.
    for manifest in intervals:
        for page in pages_by_interval[manifest.lower]:
            for change in page.changes:
                old, new = (change.before, change.after) if forward else (change.after, change.before)
                if old is not None and old.station == station:
                    members.pop(change.observation_id, None)
                if new is not None and new.station == station:
                    members[change.observation_id] = new
    return build_station_receipt(receipt_id, envelope, station, members.values())
