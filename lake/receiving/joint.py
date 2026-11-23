"""Dual-anchor, line-by-line station reconciliation from two V1 receipts.

Candidate facts may only come from anchor rows or one side of a delivered
correction page inside the two accepted pieces.  A fact can be carried to the
target revision only when its observation stays present, with identical
complete facts, at every frozen revision between its evidence position and the
target.  Sealed page attribution (including undelivered pages) comes from the
trusted issuer directory; any page attributed to the observation inside the
span breaks the continuous run.
"""
import hashlib
from dataclasses import dataclass

from lake.dispatch.envelope import decode_envelope
from lake.dispatch.joint_proof import decode_proof
from lake.receiving.store import ReceivingStore
from lake.receiving.totals import StationTotals, summarize_observations
from lake.revisions.observations import Observation, observation_order_key, require_text

# Total ordering of candidate evidence positions.  It only uses fixed
# attributes of the accepted bytes, so a reopened store makes the same pick.
_SIDE_RANK = {'after': 0, 'anchor': 1, 'before': 2}


@dataclass(frozen=True, slots=True)
class JointEvidence:
    receipt_id: int
    revision: int
    page_id: str | None
    side: str


@dataclass(frozen=True, slots=True)
class JointStationReceipt:
    day: str
    target_revision: int
    station: str
    status: str
    rows: tuple[Observation, ...] | None
    totals: StationTotals | None
    evidence: tuple[JointEvidence, ...] | None
    missing_observation_ids: tuple[str, ...]


def _candidate_key(candidate: JointEvidence) -> tuple:
    return (candidate.revision, _SIDE_RANK[candidate.side], candidate.receipt_id, candidate.page_id or '')


def _interval_index(envelope) -> dict:
    """Map each sealed interval lower revision to (manifest, delivered pages)."""
    pages_by_interval = {}
    for page in envelope.delivered_pages:
        pages_by_interval.setdefault(page.lower, []).append(page)
    return {
        manifest.lower: (manifest, tuple(pages_by_interval.get(manifest.lower, ())))
        for manifest in envelope.manifests
    }


def _anchor_rows(envelope) -> dict:
    return {
        row.observation_id: row
        for posting in envelope.anchor.postings
        for row in posting.rows
    }


def _continuous(observation_id: str, evidence_revision: int, target: int,
                intervals: dict, page_directory: dict) -> bool:
    """True when no sealed transition in the span touches the observation."""
    lower, upper = sorted((evidence_revision, target))
    for revision in range(lower, upper):
        manifest, _pages = intervals[revision]
        for entry in manifest.entries:
            attributed = page_directory.get(entry.page_id)
            if attributed is None:
                raise ValueError(f'page directory is missing sealed page {entry.page_id}')
            if attributed == observation_id:
                return False
    return True


def _gather_candidates(observation_id: str, station: str, receipt_id: int, envelope):
    candidates = []
    anchor_revision = envelope.anchor.revision
    anchor_row = _anchor_rows(envelope).get(observation_id)
    if anchor_row is not None and anchor_row.station == station:
        candidates.append((
            anchor_row,
            JointEvidence(receipt_id, anchor_revision, None, 'anchor'),
        ))
    for page in envelope.delivered_pages:
        for change in page.changes:
            if change.observation_id != observation_id:
                continue
            for side, row in (('before', change.before), ('after', change.after)):
                if row is not None and row.station == station:
                    candidates.append((
                        row,
                        JointEvidence(
                            receipt_id,
                            page.lower if side == 'before' else page.upper,
                            page.page_id,
                            side,
                        ),
                    ))
    return candidates


def lookup_joint_station(store: ReceivingStore, proof: bytes,
                         early_receipt: int, late_receipt: int,
                         station: str) -> JointStationReceipt:
    directory = decode_proof(proof)
    station = require_text(station, 'queried station')
    day = directory['day']
    target = directory['target_revision']
    page_directory = directory['page_directory']

    early_body = store.read_accepted(early_receipt)
    late_body = store.read_accepted(late_receipt)
    early_envelope = decode_envelope(early_body)
    late_envelope = decode_envelope(late_body)

    # The proof must bind the exact bytes saved under the two receipt numbers.
    for section, envelope, body in (
        (directory['early'], early_envelope, early_body),
        (directory['late'], late_envelope, late_body),
    ):
        if envelope.handoff_id != section['handoff_id']:
            raise ValueError('proof handoff ID does not match the accepted piece')
        digest = hashlib.sha256(body).hexdigest()
        if digest != section['archived_sha256'] or len(body) != section['byte_length']:
            raise ValueError('proof archival digest does not match the accepted piece')

    if early_envelope.day != day or late_envelope.day != day:
        raise ValueError('accepted piece belongs to a different day')
    if early_envelope.target_revision != target or late_envelope.target_revision != target:
        raise ValueError('accepted piece names a different target revision')
    if early_envelope.anchor.revision != directory['early']['anchor_revision']:
        raise ValueError('early anchor revision does not match the proof')
    if late_envelope.anchor.revision != directory['late']['anchor_revision']:
        raise ValueError('late anchor revision does not match the proof')
    if not early_envelope.anchor.revision < target < late_envelope.anchor.revision:
        raise ValueError('accepted anchors do not bracket the target revision')
    if early_envelope.handoff_id == late_envelope.handoff_id:
        raise ValueError('joint reconciliation requires two distinct pieces')

    # Every sealed page of both pieces must be covered by the directory.
    sealed_page_ids = set()
    for envelope in (early_envelope, late_envelope):
        for manifest in envelope.manifests:
            for entry in manifest.entries:
                sealed_page_ids.add(entry.page_id)
    unknown_pages = set(page_directory).difference(sealed_page_ids)
    if unknown_pages:
        raise ValueError(f'proof directory names an unknown page {min(unknown_pages)}')
    missing_pages = sealed_page_ids.difference(page_directory)
    if missing_pages:
        raise ValueError(f'proof directory is missing sealed page {min(missing_pages)}')
    # Delivered pages must carry the same observation as their directory entry.
    for envelope in (early_envelope, late_envelope):
        for page in envelope.delivered_pages:
            carried = tuple(change.observation_id for change in page.changes)
            if len(carried) != 1 or carried[0] != page_directory[page.page_id]:
                raise ValueError(f'proof directory mismatch for page {page.page_id}')

    early_intervals = _interval_index(early_envelope)
    late_intervals = _interval_index(late_envelope)
    spans = (
        (early_receipt, early_envelope, early_intervals),
        (late_receipt, late_envelope, late_intervals),
    )

    target_members = tuple(
        entry for entry in directory['target_rows'] if entry['station'] == station
    )
    row_by_id = {}
    evidence_by_id = {}
    missing = []
    for member in sorted(target_members, key=lambda entry: entry['observation_id']):
        observation_id = member['observation_id']
        candidates = []
        for receipt_id, envelope, intervals in spans:
            candidates.extend(_gather_candidates(observation_id, station, receipt_id, envelope))
        eligible = []
        for row, evidence in candidates:
            intervals = early_intervals if evidence.receipt_id == early_receipt else late_intervals
            if _continuous(observation_id, evidence.revision, target, intervals, page_directory):
                eligible.append((row, evidence))
        if not eligible:
            missing.append(observation_id)
            continue
        chosen_row, chosen_evidence = min(eligible, key=lambda item: _candidate_key(item[1]))
        row_by_id[observation_id] = chosen_row
        evidence_by_id[observation_id] = chosen_evidence

    if missing:
        return JointStationReceipt(
            day, target, station, 'insufficient', None, None, None, tuple(missing),
        )

    ordered_ids = tuple(sorted(row_by_id, key=lambda identifier: identifier))
    ordered_rows = tuple(row_by_id[identifier] for identifier in ordered_ids)
    ordered_evidence = tuple(evidence_by_id[identifier] for identifier in ordered_ids)
    return JointStationReceipt(
        day, target, station, 'complete', ordered_rows,
        summarize_observations(ordered_rows), ordered_evidence, (),
    )
