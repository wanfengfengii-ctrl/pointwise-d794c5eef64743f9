from dataclasses import dataclass

from lake.indexes.build import StationIndex, index_from_data, index_to_data
from lake.pages.manifest import IntervalManifest, manifest_from_data, manifest_to_data
from lake.pages.pack import CorrectionPage, canonical_json, page_from_data, page_to_data, read_json
from lake.revisions.observations import require_array, require_day, require_fields, require_number


@dataclass(frozen=True, slots=True)
class HandoffEnvelope:
    handoff_id: int
    day: str
    anchor_id: int
    anchor: StationIndex
    target_revision: int
    manifests: tuple[IntervalManifest, ...]
    delivered_pages: tuple[CorrectionPage, ...]


def encode_envelope(envelope: HandoffEnvelope) -> bytes:
    return canonical_json({
        'version': 1,
        'handoff_id': envelope.handoff_id,
        'day': envelope.day,
        'anchor_id': envelope.anchor_id,
        'anchor': index_to_data(envelope.anchor),
        'target_revision': envelope.target_revision,
        'manifests': [manifest_to_data(manifest) for manifest in envelope.manifests],
        'delivered_pages': [page_to_data(page) for page in envelope.delivered_pages],
    })


def decode_envelope(body: bytes) -> HandoffEnvelope:
    data = read_json(body)
    require_fields(data, 'version', 'handoff_id', 'day', 'anchor_id', 'anchor', 'target_revision', 'manifests', 'delivered_pages')
    if type(data['version']) is not int or data['version'] != 1:
        raise ValueError('unsupported handoff version')
    handoff_id = require_number(data['handoff_id'], 'handoff ID')
    day = require_day(data['day'])
    anchor_id = require_number(data['anchor_id'], 'anchor ID')
    anchor = index_from_data(data['anchor'])
    target = require_number(data['target_revision'], 'target revision')
    if anchor.day != day:
        raise ValueError('anchor day differs from handoff day')
    manifests = tuple(manifest_from_data(item) for item in require_array(data['manifests'], 'manifests'))
    lower, upper = sorted((anchor.revision, target))
    if len(manifests) != upper - lower:
        raise ValueError('manifest chain does not cover the handoff interval')
    for number, manifest in enumerate(manifests, start=lower):
        if (manifest.day, manifest.lower, manifest.upper) != (day, number, number + 1):
            raise ValueError('manifest chain is out of order or outside the handoff interval')
    pages = tuple(page_from_data(item) for item in require_array(data['delivered_pages'], 'delivered pages'))
    return HandoffEnvelope(handoff_id, day, anchor_id, anchor, target, manifests, pages)
