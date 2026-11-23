import json

from lake.dispatch.archive import load_issued
from lake.dispatch.envelope import decode_envelope
from lake.dispatch.issue import issue_handoff, issue_joint_proof
from lake.dispatch.transport import export_issued, read_delivery
from lake.indexes.publish import publish_index
from lake.pages.history import seal_interval
from lake.receiving.accept import accept_handoff
from lake.receiving.joint import JointEvidence, JointStationReceipt
from lake.receiving.lookup import lookup_joint_station, lookup_station
from lake.receiving.totals import StationTotals
from lake.revisions.amendments import Amendment, freeze_amendment
from lake.revisions.intake import intake_batch
from lake.revisions.observations import Observation
from tests.support import LakeTestCase, row_details

DAY = '2025-06-17'
QING = '青站'
KONG = '空站'


def fetch_evidence_row(receiving, evidence, observation_id):
    """Re-fetch the reported original row from the accepted piece itself."""
    envelope = decode_envelope(receiving.read_accepted(evidence.receipt_id))
    if evidence.side == 'anchor':
        rows = (row for posting in envelope.anchor.postings for row in posting.rows)
        return next(row for row in rows if row.observation_id == observation_id)
    page = next(page for page in envelope.delivered_pages if page.page_id == evidence.page_id)
    change = next(change for change in page.changes if change.observation_id == observation_id)
    return change.before if evidence.side == 'before' else change.after


class TestJointProof(LakeTestCase):
    def build_balanced_fixture(self):
        first = intake_batch(self.source, DAY, (
            Observation('a', QING, 7, '晨册#a'),
            Observation('b', QING, 11, '晨册#b'),
        ))
        middle = freeze_amendment(self.source, DAY, first.number, (
            Amendment('replace', 'a', Observation('a', QING, 8, '午勘#a')),
        ))
        last = freeze_amendment(self.source, DAY, middle.number, (
            Amendment('replace', 'b', Observation('b', QING, 12, '晚勘#b')),
        ))
        seal_interval(self.source, first, middle)
        seal_interval(self.source, middle, last)
        early_anchor = publish_index(self.source, DAY, first.number)
        late_anchor = publish_index(self.source, DAY, last.number)
        early = issue_handoff(self.source, DAY, early_anchor, middle.number, ())
        late = issue_handoff(self.source, DAY, late_anchor, middle.number, ())
        return first, middle, last, early, late

    def accept_pair(self, early_id, late_id):
        receiving = self.receiver()
        early_body = load_issued(self.source, early_id)
        late_body = load_issued(self.source, late_id)
        early_receipt = accept_handoff(receiving, early_body)
        late_receipt = accept_handoff(receiving, late_body)
        return receiving, early_receipt, late_receipt

    def test_sample_balances_to_19_with_dual_anchors(self):
        _, _, _, early_id, late_id = self.build_balanced_fixture()
        proof = issue_joint_proof(self.source, early_id, late_id)
        receiving, early_receipt, late_receipt = self.accept_pair(early_id, late_id)
        receiving.close()
        self.disconnect_source()
        reopened = self.receiver()
        result = lookup_joint_station(reopened, proof, early_receipt, late_receipt, QING)
        self.assertIsInstance(result, JointStationReceipt)
        self.assertEqual((result.day, result.target_revision, result.station, result.status),
                         (DAY, 2, QING, 'complete'))
        self.assertEqual(row_details(result.rows), (
            ('a', QING, 8, '午勘#a'), ('b', QING, 11, '晨册#b'),
        ))
        self.assertEqual(result.totals, StationTotals(2, 0, 19))
        self.assertEqual(result.missing_observation_ids, ())
        self.assertEqual(result.evidence, (
            JointEvidence(late_receipt, 3, None, 'anchor'),
            JointEvidence(early_receipt, 1, None, 'anchor'),
        ))

    def test_confirmed_empty_station_is_zero_account(self):
        _, _, _, early_id, late_id = self.build_balanced_fixture()
        proof = issue_joint_proof(self.source, early_id, late_id)
        receiving, early_receipt, late_receipt = self.accept_pair(early_id, late_id)
        self.disconnect_source()
        result = lookup_joint_station(receiving, proof, early_receipt, late_receipt, KONG)
        self.assertEqual((result.status, result.rows, result.evidence, result.missing_observation_ids),
                         ('complete', (), (), ()))
        self.assertEqual(result.totals, StationTotals(0, 0, 0))

    def test_evidence_positions_re_fetch_original_rows_and_survive_reopen(self):
        _, _, _, early_id, late_id = self.build_balanced_fixture()
        proof = issue_joint_proof(self.source, early_id, late_id)
        receiving, early_receipt, late_receipt = self.accept_pair(early_id, late_id)
        first = lookup_joint_station(receiving, proof, early_receipt, late_receipt, QING)
        for row, evidence in zip(first.rows, first.evidence):
            self.assertEqual(fetch_evidence_row(receiving, evidence, row.observation_id), row)
        receiving.close()
        self.disconnect_source()
        reopened = self.receiver()
        second = lookup_joint_station(reopened, proof, early_receipt, late_receipt, QING)
        self.assertEqual(second.evidence, first.evidence)
        self.assertEqual(second.rows, first.rows)
        for row, evidence in zip(second.rows, second.evidence):
            self.assertEqual(fetch_evidence_row(reopened, evidence, row.observation_id), row)

    def test_proof_carries_no_readings_or_sources(self):
        _, _, _, early_id, late_id = self.build_balanced_fixture()
        proof = issue_joint_proof(self.source, early_id, late_id)
        document = json.loads(proof)
        def walk(node):
            if isinstance(node, dict):
                for key, value in node.items():
                    self.assertNotIn(key, ('value', 'source'))
                    walk(value)
            elif isinstance(node, list):
                for item in node:
                    walk(item)
        walk(document)
        for secret in ('晨册', '午勘', '晚勘'):
            self.assertNotIn(secret.encode('utf-8'), proof)

    def test_target_only_member_seen_by_neither_anchor_is_missing(self):
        first = intake_batch(self.source, DAY, (Observation('a', QING, 7, '晨册#a'),))
        middle = freeze_amendment(self.source, DAY, 1, (
            Amendment('add', 'c', Observation('c', QING, 5, '补记#c')),
        ))
        last = freeze_amendment(self.source, DAY, 2, (Amendment('delete', 'c'),))
        seal_interval(self.source, first, middle)
        seal_interval(self.source, middle, last)
        early = issue_handoff(self.source, DAY, publish_index(self.source, DAY, 1), 2, ())
        late = issue_handoff(self.source, DAY, publish_index(self.source, DAY, 3), 2, ())
        proof = issue_joint_proof(self.source, early, late)
        receiving, early_receipt, late_receipt = self.accept_pair(early, late)
        self.disconnect_source()
        result = lookup_joint_station(receiving, proof, early_receipt, late_receipt, QING)
        self.assertEqual((result.status, result.rows, result.totals, result.evidence),
                         ('insufficient', None, None, None))
        self.assertEqual(result.missing_observation_ids, ('c',))

    def build_source_change_fixture(self):
        first = intake_batch(self.source, DAY, (Observation('a', QING, 7, '晨册#a'),))
        middle = freeze_amendment(self.source, DAY, 1, (
            Amendment('replace', 'a', Observation('a', QING, 7, '复测#a')),
            Amendment('add', 'm', Observation('m', QING, None, '补测#m')),
        ))
        last = freeze_amendment(self.source, DAY, 2, (
            Amendment('replace', 'a', Observation('a', QING, 7, '晨册#a')),
            Amendment('delete', 'm'),
        ))
        first_manifest = seal_interval(self.source, first, middle)
        second_manifest = seal_interval(self.source, middle, last)
        return first_manifest, second_manifest

    def test_source_change_and_missing_value_unproven_without_pages(self):
        self.build_source_change_fixture()
        early = issue_handoff(self.source, DAY, publish_index(self.source, DAY, 1), 2, ())
        late = issue_handoff(self.source, DAY, publish_index(self.source, DAY, 3), 2, ())
        proof = issue_joint_proof(self.source, early, late)
        receiving, early_receipt, late_receipt = self.accept_pair(early, late)
        self.disconnect_source()
        result = lookup_joint_station(receiving, proof, early_receipt, late_receipt, QING)
        self.assertEqual(result.status, 'insufficient')
        self.assertEqual((result.rows, result.totals, result.evidence), (None, None, None))
        self.assertEqual(result.missing_observation_ids, ('a', 'm'))

    def test_delivered_before_images_prove_both_rows(self):
        _, second_manifest = self.build_source_change_fixture()
        early = issue_handoff(self.source, DAY, publish_index(self.source, DAY, 1), 2, ())
        page_ids = [entry.page_id for entry in second_manifest.entries]
        late = issue_handoff(self.source, DAY, publish_index(self.source, DAY, 3), 2, page_ids)
        proof = issue_joint_proof(self.source, early, late)
        receiving, early_receipt, late_receipt = self.accept_pair(early, late)
        self.disconnect_source()
        result = lookup_joint_station(receiving, proof, early_receipt, late_receipt, QING)
        self.assertEqual(result.status, 'complete')
        self.assertEqual(row_details(result.rows), (
            ('a', QING, 7, '复测#a'), ('m', QING, None, '补测#m'),
        ))
        self.assertEqual(result.totals, StationTotals(2, 1, 7))
        self.assertEqual(result.evidence, (
            JointEvidence(late_receipt, 2, f'{DAY}/2-3/1', 'before'),
            JointEvidence(late_receipt, 2, f'{DAY}/2-3/2', 'before'),
        ))
        for row, evidence in zip(result.rows, result.evidence):
            self.assertEqual(fetch_evidence_row(receiving, evidence, row.observation_id), row)

    def test_delete_readd_delete_only_restored_after_image_counts(self):
        first = intake_batch(self.source, DAY, (Observation('a', QING, 7, '晨册#a'),))
        second = freeze_amendment(self.source, DAY, 1, (Amendment('delete', 'a'),))
        third = freeze_amendment(self.source, DAY, 2, (
            Amendment('add', 'a', Observation('a', QING, 7, '晨册#a')),
        ))
        fourth = freeze_amendment(self.source, DAY, 3, (Amendment('delete', 'a'),))
        seal_interval(self.source, first, second)
        seal_interval(self.source, second, third)
        seal_interval(self.source, third, fourth)
        early_pages = [f'{DAY}/1-2/1', f'{DAY}/2-3/1']
        early = issue_handoff(self.source, DAY, publish_index(self.source, DAY, 1), 3, early_pages)
        late = issue_handoff(self.source, DAY, publish_index(self.source, DAY, 4), 3, ())
        proof = issue_joint_proof(self.source, early, late)
        receiving, early_receipt, late_receipt = self.accept_pair(early, late)
        self.disconnect_source()
        result = lookup_joint_station(receiving, proof, early_receipt, late_receipt, QING)
        self.assertEqual(result.status, 'complete')
        self.assertEqual(row_details(result.rows), (('a', QING, 7, '晨册#a'),))
        self.assertEqual(result.totals, StationTotals(1, 0, 7))
        self.assertEqual(result.evidence, (
            JointEvidence(early_receipt, 3, f'{DAY}/2-3/1', 'after'),
        ))

    def test_station_move_cuts_continuity_until_after_image_proves_it(self):
        first = intake_batch(self.source, DAY, (Observation('a', QING, 7, '晨册#a'),))
        second = freeze_amendment(self.source, DAY, 1, (
            Amendment('replace', 'a', Observation('a', '白站', 11, '换站单#a')),
        ))
        third = freeze_amendment(self.source, DAY, 2, (
            Amendment('replace', 'a', Observation('a', '白站', 13, '白站复测#a')),
        ))
        first_manifest = seal_interval(self.source, first, second)
        seal_interval(self.source, second, third)
        early_anchor = publish_index(self.source, DAY, 1)
        late_anchor = publish_index(self.source, DAY, 3)

        bare_early = issue_handoff(self.source, DAY, early_anchor, 2, ())
        bare_late = issue_handoff(self.source, DAY, late_anchor, 2, ())
        proof = issue_joint_proof(self.source, bare_early, bare_late)
        receiving = self.receiver()
        receipts = (accept_handoff(receiving, load_issued(self.source, bare_early)),
                    accept_handoff(receiving, load_issued(self.source, bare_late)))
        white = lookup_joint_station(receiving, proof, receipts[0], receipts[1], '白站')
        self.assertEqual((white.status, white.missing_observation_ids), ('insufficient', ('a',)))
        self.assertEqual(lookup_joint_station(receiving, proof, *receipts, QING).status, 'complete')

        moved_early = issue_handoff(self.source, DAY, early_anchor, 2,
                                    [first_manifest.entries[0].page_id])
        moved_late = issue_handoff(self.source, DAY, late_anchor, 2, ())
        page_proof = issue_joint_proof(self.source, moved_early, moved_late)
        page_receipts = (accept_handoff(receiving, load_issued(self.source, moved_early)),
                         accept_handoff(receiving, load_issued(self.source, moved_late)))
        self.disconnect_source()
        proven = lookup_joint_station(receiving, page_proof, page_receipts[0], page_receipts[1], '白站')
        self.assertEqual(row_details(proven.rows), (('a', '白站', 11, '换站单#a'),))
        self.assertEqual(proven.evidence, (
            JointEvidence(page_receipts[0], 2, f'{DAY}/1-2/1', 'after'),
        ))

    def test_issue_rejects_unknown_duplicate_mismatched_and_invalid_pieces(self):
        _, _, _, early_id, late_id = self.build_balanced_fixture()
        with self.assertRaises(KeyError):
            issue_joint_proof(self.source, 404, late_id)
        with self.assertRaisesRegex(ValueError, 'distinct handoffs'):
            issue_joint_proof(self.source, early_id, early_id)
        with self.assertRaisesRegex(ValueError, 'bracket'):
            same_side = issue_handoff(self.source, DAY, publish_index(self.source, DAY, 1), 2, ())
            issue_joint_proof(self.source, early_id, same_side)
        other_day = intake_batch(self.source, '2025-06-18', (Observation('z', QING, 1, '别册#z'),))
        other_anchor = publish_index(self.source, other_day.day, 1)
        cross_day = issue_handoff(self.source, other_day.day, other_anchor, 1)
        with self.assertRaisesRegex(ValueError, 'same day'):
            issue_joint_proof(self.source, early_id, cross_day)
        backward = issue_handoff(self.source, DAY, publish_index(self.source, DAY, 3), 1)
        with self.assertRaisesRegex(ValueError, 'same target revision'):
            issue_joint_proof(self.source, early_id, backward)
        for bad in ('1', 1.0, True, None):
            with self.assertRaises(ValueError):
                issue_joint_proof(self.source, bad, late_id)

    def test_lookup_rejects_unknown_receipt_and_bad_station(self):
        _, _, _, early_id, late_id = self.build_balanced_fixture()
        proof = issue_joint_proof(self.source, early_id, late_id)
        receiving, early_receipt, late_receipt = self.accept_pair(early_id, late_id)
        with self.assertRaises(KeyError):
            lookup_joint_station(receiving, proof, 404, late_receipt, QING)
        with self.assertRaises(KeyError):
            lookup_joint_station(receiving, proof, early_receipt, 404, QING)
        for bad_station in ('', ' 青站', '青\n站', 123):
            with self.assertRaises(ValueError):
                lookup_joint_station(receiving, proof, early_receipt, late_receipt, bad_station)

    def test_lookup_rejects_malformed_proof(self):
        _, _, _, early_id, late_id = self.build_balanced_fixture()
        proof = issue_joint_proof(self.source, early_id, late_id)
        receiving, early_receipt, late_receipt = self.accept_pair(early_id, late_id)
        for bad in (b'', b'not json', b'{}', b'{"kind": "other"}', 'x', 7):
            with self.assertRaises(ValueError):
                lookup_joint_station(receiving, bad, early_receipt, late_receipt, QING)
        tampered = json.loads(proof)
        tampered['target_revision'] = 3
        with self.assertRaisesRegex(ValueError, 'bracket'):
            lookup_joint_station(
                receiving, json.dumps(tampered, sort_keys=True, separators=(',', ':')).encode(),
                early_receipt, late_receipt, QING,
            )

    def test_wrong_binding_and_retypeset_bytes_are_rejected(self):
        _, _, _, early_id, late_id = self.build_balanced_fixture()
        proof = issue_joint_proof(self.source, early_id, late_id)
        decoy = issue_handoff(self.source, DAY, publish_index(self.source, DAY, 1), 2, ())
        receiving = self.receiver()
        early_receipt = accept_handoff(receiving, load_issued(self.source, early_id))
        late_receipt = accept_handoff(receiving, load_issued(self.source, late_id))
        decoy_receipt = accept_handoff(receiving, load_issued(self.source, decoy))
        with self.assertRaisesRegex(ValueError, 'handoff ID'):
            lookup_joint_station(receiving, proof, decoy_receipt, late_receipt, QING)
        with self.assertRaisesRegex(ValueError, 'handoff ID'):
            lookup_joint_station(receiving, proof, late_receipt, early_receipt, QING)

        original = load_issued(self.source, early_id)
        retypeset = json.dumps(json.loads(original), ensure_ascii=False, indent=2).encode('utf-8')
        self.assertNotEqual(retypeset, original)
        self.assertEqual(decode_envelope(retypeset), decode_envelope(original))
        retypeset_receipt = accept_handoff(receiving, retypeset)
        with self.assertRaisesRegex(ValueError, 'digest'):
            lookup_joint_station(receiving, proof, retypeset_receipt, late_receipt, QING)
        # Identical bytes re-accepted under a new receipt number still bind.
        duplicate_receipt = accept_handoff(receiving, original)
        self.assertNotEqual(duplicate_receipt, early_receipt)
        working = lookup_joint_station(receiving, proof, duplicate_receipt, late_receipt, QING)
        self.assertEqual(working.totals, StationTotals(2, 0, 19))
        # The retypeset piece remains usable through the ordinary V1 interface.
        self.assertEqual(lookup_station(receiving, retypeset_receipt, KONG).status, 'complete')

    def test_tampered_digest_is_rejected_and_reopen_is_consistent(self):
        _, _, _, early_id, late_id = self.build_balanced_fixture()
        proof = issue_joint_proof(self.source, early_id, late_id)
        receiving, early_receipt, late_receipt = self.accept_pair(early_id, late_id)
        tampered = json.loads(proof)
        tampered['early']['archived_sha256'] = '0' * 64
        bad_proof = json.dumps(tampered, sort_keys=True, separators=(',', ':')).encode()
        with self.assertRaisesRegex(ValueError, 'digest'):
            lookup_joint_station(receiving, bad_proof, early_receipt, late_receipt, QING)
        receiving.close()
        self.disconnect_source()
        reopened = self.receiver()
        with self.assertRaisesRegex(ValueError, 'digest'):
            lookup_joint_station(reopened, bad_proof, early_receipt, late_receipt, QING)
        good = lookup_joint_station(reopened, proof, early_receipt, late_receipt, QING)
        self.assertEqual(good.totals.integer_sum, 19)

    def test_no_change_interval_keeps_continuity(self):
        first = intake_batch(self.source, DAY, (Observation('a', QING, 7, '晨册#a'),))
        second = freeze_amendment(self.source, DAY, 1, ())  # frozen, no row changes
        third = freeze_amendment(self.source, DAY, 2, (
            Amendment('replace', 'a', Observation('a', QING, 8, '午勘#a')),
        ))
        fourth = freeze_amendment(self.source, DAY, 3, (
            Amendment('replace', 'a', Observation('a', QING, 9, '晚勘#a')),
        ))
        seal_interval(self.source, first, second)   # empty manifest
        seal_interval(self.source, second, third)
        seal_interval(self.source, third, fourth)
        early = issue_handoff(self.source, DAY, publish_index(self.source, DAY, 1), 2, ())
        late = issue_handoff(self.source, DAY, publish_index(self.source, DAY, 4), 2, ())
        proof = issue_joint_proof(self.source, early, late)
        receiving, early_receipt, late_receipt = self.accept_pair(early, late)
        self.disconnect_source()
        # The empty 1->2 manifest leaves the early anchor run intact, so the
        # rev-1 row proves the identical target rev-2 row; the rev-4 anchor
        # holds a different value and cannot substitute for it.
        result = lookup_joint_station(receiving, proof, early_receipt, late_receipt, QING)
        self.assertEqual(row_details(result.rows), (('a', QING, 7, '晨册#a'),))
        self.assertEqual(result.evidence, (
            JointEvidence(early_receipt, 1, None, 'anchor'),
        ))

    def test_tied_candidate_positions_choose_deterministically(self):
        # a is unchanged at the target, so both anchors carry an identical row.
        first = intake_batch(self.source, DAY, (Observation('a', QING, 7, '晨册#a'),))
        second = freeze_amendment(self.source, DAY, 1, (
            Amendment('add', 'b', Observation('b', '红站', 3, '红册#b')),
        ))
        third = freeze_amendment(self.source, DAY, 2, (
            Amendment('replace', 'b', Observation('b', '红站', 5, '红勘#b')),
        ))
        seal_interval(self.source, first, second)
        seal_interval(self.source, second, third)
        early = issue_handoff(self.source, DAY, publish_index(self.source, DAY, 1), 2)
        late = issue_handoff(self.source, DAY, publish_index(self.source, DAY, 3), 2)
        proof = issue_joint_proof(self.source, early, late)
        receiving, early_receipt, late_receipt = self.accept_pair(early, late)
        first_pick = lookup_joint_station(receiving, proof, early_receipt, late_receipt, QING)
        receiving.close()
        self.disconnect_source()
        reopened = self.receiver()
        second_pick = lookup_joint_station(reopened, proof, early_receipt, late_receipt, QING)
        # revision 1 anchor vs revision 3 anchor: lower revision wins.
        self.assertEqual(first_pick.evidence, second_pick.evidence)
        self.assertEqual(first_pick.evidence, (
            JointEvidence(early_receipt, 1, None, 'anchor'),
        ))
        self.assertEqual(row_details(first_pick.rows), (('a', QING, 7, '晨册#a'),))
        self.assertEqual(first_pick.totals, StationTotals(1, 0, 7))
