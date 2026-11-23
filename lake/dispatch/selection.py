from dataclasses import dataclass

from lake.indexes.build import StationIndex
from lake.indexes.publish import load_index
from lake.pages.history import load_interval_chain, load_page
from lake.pages.manifest import IntervalManifest
from lake.pages.pack import CorrectionPage
from lake.revisions.observations import require_day, require_text
from lake.revisions.store import SourceStore


@dataclass(frozen=True, slots=True)
class ShipmentSelection:
    anchor_id: int
    anchor: StationIndex
    target_revision: int
    manifests: tuple[IntervalManifest, ...]
    delivered_pages: tuple[CorrectionPage, ...]


def select_materials(store: SourceStore, day: str, anchor_id: int, target_revision: int, page_ids=None) -> ShipmentSelection:
    require_day(day)
    anchor = load_index(store, anchor_id)
    if anchor.day != day:
        raise ValueError('anchor belongs to a different day')
    manifests = load_interval_chain(store, day, anchor.revision, target_revision)
    expected = tuple(entry.page_id for manifest in manifests for entry in manifest.entries)
    if page_ids is None:
        requested = set(expected)
    else:
        if isinstance(page_ids, (str, bytes)):
            raise ValueError('page IDs require a sequence')
        items = tuple(require_text(page_id, 'requested page ID') for page_id in page_ids)
        requested = set(items)
        if len(requested) != len(items):
            raise ValueError('duplicate requested page ID')
        outside = requested.difference(expected)
        if outside:
            raise ValueError(f'page outside handoff interval: {min(outside)}')
    delivered = tuple(load_page(store, page_id) for page_id in expected if page_id in requested)
    return ShipmentSelection(anchor_id, anchor, target_revision, manifests, delivered)
