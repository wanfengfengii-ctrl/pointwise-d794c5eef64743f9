import json
from pathlib import Path
from tempfile import TemporaryDirectory

from lake.dispatch.archive import initialize_dispatch_archive
from lake.dispatch.issue import issue_handoff
from lake.dispatch.transport import export_issued, read_delivery
from lake.indexes.publish import initialize_indexes, publish_index
from lake.indexes.stations import initialize_station_roster
from lake.pages.history import initialize_page_history, seal_interval
from lake.receiving.accept import accept_handoff
from lake.receiving.lookup import lookup_station
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


def main():
    with TemporaryDirectory() as work:
        directory = Path(work)
        source_path = directory / "source.sqlite"
        receiving_path = directory / "receiving.sqlite"
        shipments = (directory / "early.handoff", directory / "late.handoff")
        with SourceStore(source_path) as source:
            for handoff_id, path in zip(prepare_pair(source), shipments):
                export_issued(source, handoff_id, path)
        source_path.unlink()

        with ReceivingStore(receiving_path) as receiving:
            receiving.initialize()
            receipts = tuple(accept_handoff(receiving, read_delivery(path)) for path in shipments)
        with ReceivingStore(receiving_path) as receiving:
            results = v1_results(receiving, receipts)
        print(json.dumps({"day": DAY, "target_revision": 2, "station": STATION, "v1": results},
                         ensure_ascii=False))


if __name__ == "__main__":
    main()
