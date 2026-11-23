from lake.pages.history import load_interval_manifest, load_page, seal_interval
from lake.pages.manifest import decode_manifest, encode_manifest
from lake.pages.pack import decode_page, encode_page
from lake.revisions.amendments import Amendment, freeze_amendment
from lake.revisions.observations import Observation
from lake.revisions.snapshots import load_revision
from tests.support import LakeTestCase, row_details


class TestCorrectionPages(LakeTestCase):
    def test_move_manifest(self):
        (first, second, _), (manifest, _) = self.move_history()
        move = manifest.entries[0]
        self.assertEqual(move.page_id, '2025-06-13/1-2/1')
        self.assertEqual(move.affected_stations, ('白站', '青站'))
        page = load_page(self.source, move.page_id)
        self.assertEqual(row_details((page.changes[0].before, page.changes[0].after)), (
            ('a', '青站', 7, '青站晨册#a'), ('a', '白站', 11, '换站单#a'),
        ))
        self.assertEqual(decode_page(encode_page(page)), page)
        self.assertEqual(decode_manifest(encode_manifest(manifest)), manifest)
        self.assertEqual(seal_interval(self.source, first, second), manifest)
        self.assertEqual(load_revision(self.source, first.day, 1).observations[0].station, '青站')
        self.assertEqual(load_revision(self.source, first.day, 2).observations[0].station, '白站')

    def test_amendment_history(self):
        initial = self.intake()
        day = initial.day
        updated = freeze_amendment(self.source, day, 1, [
            Amendment('add', 'x', Observation('x', '红站', None, '补登#x')),
            Amendment('delete', 'm'),
            Amendment('replace', 'z', Observation('z', '青站', -2, '更值#z')),
            Amendment('replace', 'b', Observation('b', '白站', 9, '移站#b')),
        ])
        self.assertEqual(row_details(updated.observations), (
            ('b', '白站', 9, '移站#b'), ('x', '红站', None, '补登#x'), ('z', '青站', -2, '更值#z'),
        ))
        self.assertEqual(load_revision(self.source, day, 1), initial)
        manifest = seal_interval(self.source, initial, updated)
        changes = {
            change.observation_id: change
            for entry in manifest.entries
            for change in load_page(self.source, entry.page_id).changes
        }
        self.assertEqual(tuple(changes), ('b', 'm', 'x', 'z'))
        self.assertEqual((changes['b'].before.station, changes['b'].after.station), ('青站', '白站'))
        self.assertEqual((changes['z'].before.value, changes['z'].after.value), (0, -2))
        self.assertIsNone(changes['m'].after)
        self.assertIsNone(changes['m'].before.value)
        self.assertIsNone(changes['x'].before)
        self.assertIsNone(changes['x'].after.value)
        with self.assertRaises(ValueError):
            freeze_amendment(self.source, day, 2, [Amendment('delete', 'x'), Amendment('delete', 'missing')])
        with self.assertRaises(ValueError):
            freeze_amendment(self.source, day, 20, [Amendment('delete', 'x')])
        with self.assertRaises(KeyError):
            load_revision(self.source, day, 3)
        self.assertEqual(load_revision(self.source, day, 2), updated)

    def test_invalid_interval(self):
        ordinary = self.intake()
        (first, second, third), _ = self.move_history()
        with self.assertRaises(ValueError):
            seal_interval(self.source, ordinary, second)
        with self.assertRaises(ValueError):
            seal_interval(self.source, first, third)
        with self.assertRaises(KeyError):
            load_interval_manifest(self.source, first.day, 1, 3)
        self.assertEqual(self.source.connection.execute('SELECT COUNT(*) FROM sealed_manifests').fetchone()[0], 2)
        self.assertEqual(self.source.connection.execute('SELECT COUNT(*) FROM sealed_pages').fetchone()[0], 3)

    def test_sealed_no_change_interval(self):
        first = self.intake()
        second = freeze_amendment(self.source, first.day, 1, [])
        manifest = seal_interval(self.source, first, second)
        self.assertEqual(manifest.entries, ())
        self.assertEqual(load_interval_manifest(self.source, first.day, 1, 2), manifest)
