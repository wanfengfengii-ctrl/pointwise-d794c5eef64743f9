"""Trusted directory credential binding two formally issued V1 handoffs.

The joint proof is handed to the night auditor separately from the two
shipments.  It only carries qualification facts (identities, positions,
membership, page/observation mapping and archival byte digests); observation
readings and original source references stay inside the V1 envelope bodies.
"""
import hashlib

from lake.dispatch.archive import load_issued
from lake.dispatch.envelope import decode_envelope
from lake.pages.history import load_page
from lake.pages.pack import canonical_json, read_json
from lake.revisions.observations import (
    require_array,
    require_day,
    require_fields,
    require_number,
    require_text,
)
from lake.revisions.snapshots import load_revision
from lake.revisions.store import SourceStore

PROOF_KIND = 'joint_lookup_proof'


def _digest(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def _section(handoff_id: int, anchor_revision: int, body: bytes) -> dict:
    return {
        'handoff_id': handoff_id,
        'anchor_revision': anchor_revision,
        'archived_sha256': _digest(body),
        'byte_length': len(body),
    }


def _sealed_page_observations(store: SourceStore, envelope) -> dict:
    """Map every sealed page in the piece's intervals to its observation ID.

    V1 correction pages are packed with exactly one observation change each.
    Undelivered pages are not present in the envelope bytes, so the trusted
    issuer directory is the only place their attribution can be recorded.
    """
    mapping = {}
    for manifest in envelope.manifests:
        for entry in manifest.entries:
            page = load_page(store, entry.page_id)
            identifiers = tuple(change.observation_id for change in page.changes)
            if len(identifiers) != 1:
                raise ValueError('joint proof supports single-observation V1 pages')
            if entry.page_id in mapping:
                raise ValueError(f'duplicate sealed page {entry.page_id}')
            mapping[entry.page_id] = identifiers[0]
    return mapping


def issue_joint_proof(store: SourceStore, early_id: int, late_id: int) -> bytes:
    """Build the trusted directory credential for two issued V1 handoffs."""
    require_number(early_id, 'early handoff ID')
    require_number(late_id, 'late handoff ID')
    if early_id == late_id:
        raise ValueError('joint proof requires two distinct handoffs')
    early_body = load_issued(store, early_id)
    late_body = load_issued(store, late_id)
    early = decode_envelope(early_body)
    late = decode_envelope(late_body)
    if early.day != late.day:
        raise ValueError('joint proof handoffs must belong to the same day')
    day = require_day(early.day)
    if early.target_revision != late.target_revision:
        raise ValueError('joint proof handoffs must name the same target revision')
    target = require_number(early.target_revision, 'target revision')
    early_anchor = require_number(early.anchor.revision, 'early anchor revision')
    late_anchor = require_number(late.anchor.revision, 'late anchor revision')
    if not early_anchor < target < late_anchor:
        raise ValueError('anchors must strictly bracket the target revision')
    if late.anchor.revision != late_anchor:
        raise ValueError('late anchor revision mismatch')
    if early.anchor_id == late.anchor_id:
        raise ValueError('joint proof requires two distinct anchor indexes')

    # Target membership comes from the formally frozen target revision; only
    # identity and station are recorded, never readings or source references.
    target_snapshot = load_revision(store, day, target)
    target_rows = [
        {'observation_id': row.observation_id, 'station': row.station}
        for row in sorted(target_snapshot.observations, key=lambda row: row.observation_id)
    ]

    pages = {}
    for envelope in (early, late):
        interval_pages = _sealed_page_observations(store, envelope)
        overlap = set(pages).intersection(interval_pages)
        if overlap:
            raise ValueError(f'page appears in both handoffs: {min(overlap)}')
        pages.update(interval_pages)
    # Delivered pages must agree with the sealed attribution.
    for envelope in (early, late):
        for page in envelope.delivered_pages:
            carried = tuple(change.observation_id for change in page.changes)
            if len(carried) != 1 or pages.get(page.page_id) != carried[0]:
                raise ValueError(f'delivered page {page.page_id} disagrees with sealed history')
    page_directory = [
        {'page_id': page_id, 'observation_id': pages[page_id]}
        for page_id in sorted(pages)
    ]

    document = {
        'kind': PROOF_KIND,
        'version': 1,
        'day': day,
        'target_revision': target,
        'early': _section(early_id, early_anchor, early_body),
        'late': _section(late_id, late_anchor, late_body),
        'target_rows': target_rows,
        'page_directory': page_directory,
    }
    return canonical_json(document)


def decode_proof(proof: bytes) -> dict:
    if type(proof) is not bytes or not proof:
        raise ValueError('joint proof credential must be nonempty bytes')
    try:
        data = read_json(proof)
    except (UnicodeDecodeError, ValueError) as exc:
        raise ValueError('joint proof credential is not valid JSON') from exc
    require_fields(
        data, 'kind', 'version', 'day', 'target_revision',
        'early', 'late', 'target_rows', 'page_directory',
    )
    if data.get('kind') != PROOF_KIND or type(data.get('version')) is not int or data['version'] != 1:
        raise ValueError('unsupported joint proof credential')
    day = require_day(data['day'])
    target = require_number(data['target_revision'], 'target revision')

    sections = {}
    for label in ('early', 'late'):
        section = data[label]
        require_fields(section, 'handoff_id', 'anchor_revision', 'archived_sha256', 'byte_length')
        handoff_id = require_number(section['handoff_id'], f'{label} handoff ID')
        anchor_revision = require_number(section['anchor_revision'], f'{label} anchor revision')
        digest = section['archived_sha256']
        if type(digest) is not str or len(digest) != 64 or any(char not in '0123456789abcdef' for char in digest):
            raise ValueError(f'invalid {label} archival digest')
        byte_length = section['byte_length']
        if type(byte_length) is not int or byte_length < 0:
            raise ValueError(f'invalid {label} byte length')
        sections[label] = {
            'handoff_id': handoff_id,
            'anchor_revision': anchor_revision,
            'archived_sha256': digest,
            'byte_length': byte_length,
        }
    if sections['early']['handoff_id'] == sections['late']['handoff_id']:
        raise ValueError('joint proof requires two distinct handoffs')
    if not sections['early']['anchor_revision'] < target < sections['late']['anchor_revision']:
        raise ValueError('anchors must strictly bracket the target revision')

    target_rows = []
    seen_identifiers = set()
    for entry in require_array(data['target_rows'], 'target rows'):
        require_fields(entry, 'observation_id', 'station')
        observation_id = require_text(entry['observation_id'], 'target observation ID')
        station = require_text(entry['station'], 'target station')
        if observation_id in seen_identifiers:
            raise ValueError(f'duplicate target member {observation_id!r}')
        seen_identifiers.add(observation_id)
        target_rows.append({'observation_id': observation_id, 'station': station})
    if [row['observation_id'] for row in target_rows] != sorted(seen_identifiers):
        raise ValueError('target member ordering is not canonical')

    page_directory = {}
    for entry in require_array(data['page_directory'], 'page directory'):
        require_fields(entry, 'page_id', 'observation_id')
        page_id = require_text(entry['page_id'], 'page ID')
        observation_id = require_text(entry['observation_id'], 'page observation ID')
        if page_id in page_directory:
            raise ValueError(f'duplicate page directory entry {page_id!r}')
        page_directory[page_id] = observation_id

    return {
        'day': day,
        'target_revision': target,
        'early': sections['early'],
        'late': sections['late'],
        'target_rows': tuple(target_rows),
        'page_directory': page_directory,
    }
