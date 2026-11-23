from lake.dispatch.archive import load_issued
from lake.dispatch.envelope import decode_envelope, encode_envelope
from lake.dispatch.issue import issue_handoff
from lake.dispatch.transport import export_issued, read_delivery
from lake.indexes.publish import publish_index
from lake.receiving.accept import accept_handoff
from lake.receiving.lookup import lookup_station
from tests.support import LakeTestCase, row_details


class TestDispatch(LakeTestCase):
    def test_cross_day_page(self):
        ordinary = self.intake()
        (move, _, _), (manifest, _) = self.move_history()
        anchor = publish_index(self.source, ordinary.day, 1)
        with self.assertRaisesRegex(ValueError, 'outside handoff interval'):
            issue_handoff(self.source, ordinary.day, anchor, 1, [manifest.entries[0].page_id])
        with self.assertRaisesRegex(ValueError, 'different day'):
            issue_handoff(self.source, move.day, anchor, 1)
        with self.assertRaises(KeyError):
            load_issued(self.source, 1)

    def test_same_day_outside_interval(self):
        (old, middle, _), (first, second) = self.move_history()
        anchor = publish_index(self.source, old.day, old.number)
        with self.assertRaisesRegex(ValueError, 'outside handoff interval'):
            issue_handoff(self.source, old.day, anchor, middle.number, [second.entries[0].page_id])
        with self.assertRaises(KeyError):
            load_issued(self.source, 1)
        issued = issue_handoff(self.source, old.day, anchor, middle.number, [first.entries[0].page_id])
        envelope = decode_envelope(load_issued(self.source, issued))
        self.assertEqual(tuple(page.page_id for page in envelope.delivered_pages), (first.entries[0].page_id,))
        self.assertEqual(len(envelope.manifests[0].entries), 2)

    def test_same_day_move_handoff(self):
        (old, middle, _), (first, _) = self.move_history()
        anchor = publish_index(self.source, old.day, 1)
        body = self.issue_body(old.day, anchor, middle.number)
        envelope = decode_envelope(body)
        self.assertEqual((envelope.anchor.revision, envelope.target_revision), (1, 2))
        self.assertEqual([page.page_id for page in envelope.delivered_pages], [entry.page_id for entry in first.entries])
        self.assertEqual(envelope.manifests[0].entries[0].affected_stations, ('白站', '青站'))
        change = envelope.delivered_pages[0].changes[0]
        self.assertEqual(row_details((change.before, change.after)), (
            ('a', '青站', 7, '青站晨册#a'), ('a', '白站', 11, '换站单#a'),
        ))
        receiving = self.receiver()
        accepted = accept_handoff(receiving, body)
        self.assertEqual(row_details(lookup_station(receiving, accepted, '白站').rows), (('a', '白站', 11, '换站单#a'),))

    def test_transport_roundtrip(self):
        (old, middle, _), _ = self.move_history()
        anchor = publish_index(self.source, old.day, 1)
        identifier = issue_handoff(self.source, old.day, anchor, middle.number)
        archived = load_issued(self.source, identifier)
        path = self.directory / 'shift.handoff'
        path.write_bytes(b'older transport file')
        self.assertEqual(export_issued(self.source, identifier, path), path)
        self.assertEqual(read_delivery(path), archived)
        envelope = decode_envelope(archived)
        self.assertEqual(envelope.handoff_id, identifier)
        self.assertEqual(encode_envelope(envelope), archived)
        self.assertEqual(len(envelope.delivered_pages), 2)
        self.disconnect_source()
        receiving = self.receiver()
        receipt_id = accept_handoff(receiving, read_delivery(path))
        self.assertEqual(receiving.read_accepted(receipt_id), archived)
