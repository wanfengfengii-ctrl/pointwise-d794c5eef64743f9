from lake.indexes.publish import publish_index
from lake.pages.history import seal_interval
from lake.receiving.accept import accept_handoff
from lake.receiving.lookup import lookup_station
from lake.receiving.totals import StationTotals
from lake.revisions.amendments import Amendment, freeze_amendment
from lake.revisions.observations import Observation
from tests.support import LakeTestCase, row_details


class TestStationHandoff(LakeTestCase):
    def ordinary_body(self):
        first = self.intake()
        anchor = publish_index(self.source, first.day, 1)
        return first, self.issue_body(first.day, anchor, 1)

    def test_equal_anchor(self):
        _, body = self.ordinary_body()
        receiving = self.receiver()
        receipt_id = accept_handoff(receiving, body)
        receiving.close()
        self.disconnect_source()
        reopened = self.receiver()
        result = lookup_station(reopened, receipt_id, '青站')
        self.assertEqual((result.receipt_id, result.handoff_id, result.day, result.target_revision, result.status),
                         (receipt_id, 1, '2025-06-12', 1, 'complete'))
        self.assertEqual(row_details(result.rows), (
            ('b', '青站', 7, '青站晨册#b'), ('m', '青站', None, '青站晨册#m'), ('z', '青站', 0, '青站晨册#z'),
        ))
        self.assertEqual(result.totals, StationTotals(3, 1, 7))

    def test_known_empty(self):
        _, body = self.ordinary_body()
        receiving = self.receiver()
        identifier = accept_handoff(receiving, body)
        result = lookup_station(receiving, identifier, '空站')
        self.assertEqual((result.status, result.rows, result.totals, result.missing_pages),
                         ('complete', (), StationTotals(0, 0, 0), ()))
        self.assertEqual(lookup_station(receiving, identifier, '未登记站').rows, ())

    def test_unknown_receipt(self):
        receiving = self.receiver()
        with self.assertRaises(KeyError):
            lookup_station(receiving, 404, '青站')

    def test_reopen_after_amendment(self):
        first, body = self.ordinary_body()
        receiving = self.receiver()
        receipt_id = accept_handoff(receiving, body)
        initial = lookup_station(receiving, receipt_id, '青站')
        receiving.close()
        later = freeze_amendment(self.source, first.day, 1, [
            Amendment('replace', 'b', Observation('b', '青站', 21, '校勘#b')),
        ])
        later_anchor = publish_index(self.source, first.day, later.number)
        later_body = self.issue_body(first.day, later_anchor, later.number)
        self.disconnect_source()
        reopened = self.receiver()
        reopened.initialize()
        self.assertEqual(lookup_station(reopened, receipt_id, '青站'), initial)
        self.assertEqual(reopened.read_accepted(receipt_id), body)
        other = accept_handoff(reopened, later_body)
        self.assertEqual(lookup_station(reopened, other, '青站').totals, StationTotals(3, 1, 21))
        self.assertEqual(lookup_station(reopened, receipt_id, '青站').totals, StationTotals(3, 1, 7))

    def test_move_both_directions(self):
        (first, middle, _), _ = self.move_history()
        old_anchor = publish_index(self.source, first.day, 1)
        late_anchor = publish_index(self.source, first.day, 2)
        forward_body = self.issue_body(first.day, old_anchor, middle.number)
        backward_body = self.issue_body(first.day, late_anchor, first.number)
        receiving = self.receiver()
        forward = accept_handoff(receiving, forward_body)
        backward = accept_handoff(receiving, backward_body)
        receiving.close()
        self.disconnect_source()
        offline = self.receiver()
        def result(receipt_id, station):
            entry = lookup_station(offline, receipt_id, station)
            details = None if entry.rows is None else row_details(entry.rows)
            return entry.status, entry.target_revision, details, entry.totals
        self.assertEqual((result(forward, '白站'), result(forward, '青站'),
                          result(backward, '青站'), result(backward, '白站')), (
            ('complete', 2, (('a', '白站', 11, '换站单#a'),), StationTotals(1, 0, 11)),
            ('complete', 2, (), StationTotals(0, 0, 0)),
            ('complete', 1, (('a', '青站', 7, '青站晨册#a'),), StationTotals(1, 0, 7)),
            ('complete', 1, (), StationTotals(0, 0, 0)),
        ), 'TARGET_STATION_MEMBERSHIP_DRIFT')

    def test_move_then_value(self):
        (first, _, last), _ = self.move_history()
        old = publish_index(self.source, first.day, first.number)
        new = publish_index(self.source, first.day, last.number)
        receiving = self.receiver()
        forward = accept_handoff(receiving, self.issue_body(first.day, old, last.number))
        backward = accept_handoff(receiving, self.issue_body(first.day, new, first.number))
        self.disconnect_source()
        for identifier, station, other, value, source in (
            (forward, '白站', '青站', 13, '白站复测#a'),
            (backward, '青站', '白站', 7, '青站晨册#a'),
        ):
            with self.subTest(receipt=identifier):
                result = lookup_station(receiving, identifier, station)
                self.assertEqual(row_details(result.rows), (('a', station, value, source),))
                self.assertEqual(result.totals, StationTotals(1, 0, value))
                self.assertEqual(lookup_station(receiving, identifier, other).rows, ())

    def test_station_local_missing_page(self):
        (first, middle, _), (manifest, _) = self.move_history()
        anchor = publish_index(self.source, first.day, 1)
        receiving = self.receiver()
        receipt_id = accept_handoff(receiving, self.issue_body(first.day, anchor, middle.number, []))
        self.disconnect_source()
        for station in ('青站', '白站', '红站'):
            result = lookup_station(receiving, receipt_id, station)
            self.assertEqual((result.status, result.rows, result.totals), ('insufficient', None, None))
            expected_page = manifest.entries[1 if station == '红站' else 0].page_id
            self.assertEqual(result.missing_pages, (expected_page,))
        self.assertEqual(lookup_station(receiving, receipt_id, '空站').totals, StationTotals(0, 0, 0))

    def test_red_correction(self):
        (first, _, last), (first_manifest, last_manifest) = self.move_history()
        pages = [first_manifest.entries[1].page_id, last_manifest.entries[0].page_id]
        old = publish_index(self.source, first.day, first.number)
        new = publish_index(self.source, first.day, last.number)
        receiving = self.receiver()
        forward = accept_handoff(receiving, self.issue_body(first.day, old, last.number, pages))
        backward = accept_handoff(receiving, self.issue_body(first.day, new, first.number, pages))
        self.disconnect_source()
        for identifier, reading, evidence in ((forward, 5, '红站更正#r'), (backward, 3, '红站晨册#r')):
            for station in ('青站', '白站'):
                result = lookup_station(receiving, identifier, station)
                self.assertEqual((result.status, result.rows, result.totals, result.missing_pages),
                                 ('insufficient', None, None, (first_manifest.entries[0].page_id,)))
            red = lookup_station(receiving, identifier, '红站')
            self.assertEqual((red.status, row_details(red.rows), red.totals),
                             ('complete', (('r', '红站', reading, evidence),), StationTotals(1, 0, reading)))
            empty = lookup_station(receiving, identifier, '空站')
            self.assertEqual((empty.status, empty.rows, empty.totals), ('complete', (), StationTotals(0, 0, 0)))

    def test_add_and_delete_with_missing_value(self):
        first = self.intake()
        second = freeze_amendment(self.source, first.day, 1, [
            Amendment('delete', 'm'), Amendment('add', 'x', Observation('x', '白站', None, '补记#x')),
        ])
        seal_interval(self.source, first, second)
        old = publish_index(self.source, first.day, 1)
        new = publish_index(self.source, first.day, 2)
        receiving = self.receiver()
        forward = accept_handoff(receiving, self.issue_body(first.day, old, 2))
        backward = accept_handoff(receiving, self.issue_body(first.day, new, 1))
        self.disconnect_source()
        added = lookup_station(receiving, forward, '白站')
        self.assertEqual(row_details(added.rows), (('x', '白站', None, '补记#x'),))
        self.assertEqual(added.totals, StationTotals(1, 1, 0))
        self.assertEqual([row.observation_id for row in lookup_station(receiving, forward, '青站').rows], ['b', 'z'])
        self.assertEqual(lookup_station(receiving, backward, '白站').rows, ())
        restored = lookup_station(receiving, backward, '青站')
        self.assertEqual(row_details(restored.rows)[1], ('m', '青站', None, '青站晨册#m'))
        self.assertEqual(restored.totals, StationTotals(3, 1, 7))
