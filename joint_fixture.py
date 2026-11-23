import json
from dataclasses import asdict
from pathlib import Path
from tempfile import TemporaryDirectory

from lake.dispatch.archive import initialize_dispatch_archive
from lake.dispatch.envelope import decode_envelope
from lake.dispatch.issue import issue_handoff, issue_joint_proof
from lake.dispatch.transport import export_issued, read_delivery
from lake.indexes.publish import initialize_indexes, publish_index
from lake.indexes.stations import initialize_station_roster
from lake.pages.history import initialize_page_history, seal_interval
from lake.receiving.accept import accept_handoff
from lake.receiving.lookup import lookup_joint_station, lookup_station
from lake.receiving.store import ReceivingStore
from lake.revisions.amendments import Amendment, freeze_amendment
from lake.revisions.intake import intake_batch
from lake.revisions.observations import Observation
from lake.revisions.store import SourceStore

DAY = "2025-06-17"
STATION = "青站"


def prepare_pair(source):
    """Build and issue the two ordinary V1 handoffs in an empty source store."""
    source.initialize()
    initialize_station_roster(source, (STATION, "空站"))
    initialize_indexes(source)
    initialize_page_history(source)
    initialize_dispatch_archive(source)

    first = intake_batch(source, DAY, (
        Observation("a", STATION, 7, "晨册#a"),
        Observation("b", STATION, 11, "晨册#b"),
    ))
    middle = freeze_amendment(source, DAY, first.number, (
        Amendment("replace", "a", Observation("a", STATION, 8, "午勘#a")),
    ))
    last = freeze_amendment(source, DAY, middle.number, (
        Amendment("replace", "b", Observation("b", STATION, 12, "晚勘#b")),
    ))
    seal_interval(source, first, middle)
    seal_interval(source, middle, last)
    early_anchor = publish_index(source, DAY, first.number)
    late_anchor = publish_index(source, DAY, last.number)
    return (
        issue_handoff(source, DAY, early_anchor, middle.number, ()),
        issue_handoff(source, DAY, late_anchor, middle.number, ()),
    )


def v1_results(receiving, receipt_ids):
    summaries = []
    for receipt_id in receipt_ids:
        result = lookup_station(receiving, receipt_id, STATION)
        expected = ("insufficient", 2, None, None)
        actual = (result.status, result.target_revision, result.rows, result.totals)
        if actual != expected or len(result.missing_pages) != 1:
            raise AssertionError(f"Unexpected V1 receipt: {result!r}")
        summaries.append({
            "receipt_id": receipt_id,
            "handoff_id": result.handoff_id,
            "status": result.status,
            "missing_pages": result.missing_pages,
        })
    return summaries


def _evidence_row(receiving, evidence, observation_id):
    """Re-fetch the original row at the evidence position inside the piece."""
    envelope = decode_envelope(receiving.read_accepted(evidence.receipt_id))
    if evidence.side == "anchor":
        rows = (row for posting in envelope.anchor.postings for row in posting.rows)
        return next(row for row in rows if row.observation_id == observation_id)
    page = next(page for page in envelope.delivered_pages if page.page_id == evidence.page_id)
    change = next(change for change in page.changes if change.observation_id == observation_id)
    return change.before if evidence.side == "before" else change.after


def joint_results(receiving, proof, early_receipt, late_receipt):
    result = lookup_joint_station(receiving, proof, early_receipt, late_receipt, STATION)
    if result.totals.integer_sum != 19 or result.totals.row_count != 2:
        raise AssertionError(f"Unexpected joint totals: {result.totals!r}")
    evidence = []
    for row, item in zip(result.rows, result.evidence):
        if _evidence_row(receiving, item, row.observation_id) != row:
            raise AssertionError(f"Evidence position does not return the reported row: {item!r}")
        evidence.append({
            "observation_id": row.observation_id,
            "receipt_id": item.receipt_id,
            "revision": item.revision,
            "page_id": item.page_id,
            "side": item.side,
        })
    empty = lookup_joint_station(receiving, proof, early_receipt, late_receipt, "空站")
    if empty.status != "complete" or empty.rows or empty.evidence or empty.totals.integer_sum:
        raise AssertionError(f"Unexpected empty station result: {empty!r}")
    return {
        "status": result.status,
        "rows": [[row.observation_id, row.station, row.value, row.source] for row in result.rows],
        "totals": asdict(result.totals),
        "evidence": evidence,
        "empty_station": {"status": empty.status, "rows": [], "totals": asdict(empty.totals)},
    }


def main():
    with TemporaryDirectory() as work:
        directory = Path(work)
        source_path = directory / "source.sqlite"
        receiving_path = directory / "receiving.sqlite"
        shipments = (directory / "early.handoff", directory / "late.handoff")
        with SourceStore(source_path) as source:
            early_id, late_id = prepare_pair(source)
            proof = issue_joint_proof(source, early_id, late_id)
            for handoff_id, path in zip((early_id, late_id), shipments):
                export_issued(source, handoff_id, path)
        source_path.unlink()

        with ReceivingStore(receiving_path) as receiving:
            receiving.initialize()
            receipts = tuple(accept_handoff(receiving, read_delivery(path)) for path in shipments)
        with ReceivingStore(receiving_path) as receiving:
            v1 = v1_results(receiving, receipts)
            joint = joint_results(receiving, proof, receipts[0], receipts[1])
        print(json.dumps({
            "day": DAY, "target_revision": 2, "station": STATION,
            "v1": v1, "joint": joint,
        }, ensure_ascii=False))


if __name__ == "__main__":
    main()
