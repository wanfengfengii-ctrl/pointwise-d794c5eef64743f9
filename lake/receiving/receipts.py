from dataclasses import dataclass

from lake.dispatch.envelope import HandoffEnvelope, decode_envelope
from lake.receiving.store import ReceivingStore
from lake.receiving.totals import StationTotals, summarize_observations
from lake.revisions.observations import Observation, observation_order_key


@dataclass(frozen=True, slots=True)
class StationReceipt:
    receipt_id: int
    handoff_id: int
    day: str
    target_revision: int
    station: str
    status: str
    rows: tuple[Observation, ...] | None
    totals: StationTotals | None
    missing_pages: tuple[str, ...]


def load_accepted_handoff(store: ReceivingStore, receipt_id: int) -> HandoffEnvelope:
    return decode_envelope(store.read_accepted(receipt_id))


def build_station_receipt(receipt_id: int, envelope: HandoffEnvelope, station: str, rows, missing_pages=()) -> StationReceipt:
    missing = tuple(missing_pages)
    if missing:
        if rows is not None:
            raise ValueError('unproven station receipt cannot include complete rows')
        status, ordered, totals = 'insufficient', None, None
    else:
        if rows is None:
            raise ValueError('complete station receipt requires rows, possibly empty')
        ordered = tuple(sorted(rows, key=observation_order_key))
        if any(row.station != station for row in ordered):
            raise ValueError('station receipt contains a row from a different station')
        status, totals = 'complete', summarize_observations(ordered)
    return StationReceipt(
        receipt_id, envelope.handoff_id, envelope.day, envelope.target_revision,
        station, status, ordered, totals, missing,
    )
