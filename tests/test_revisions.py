from lake.dispatch.archive import load_issued
from lake.indexes.postings import ordered_members
from lake.indexes.publish import load_index, publish_index
from lake.indexes.stations import load_station_roster
from lake.pages.history import load_interval_manifest, load_page
from lake.revisions.amendments import Amendment, freeze_amendment
from lake.revisions.intake import intake_batch
from lake.revisions.observations import Observation
from lake.revisions.snapshots import load_revision
from lake.revisions.store import SourceStore
from tests.support import LakeTestCase, initialize_source, row_details


class TestDayRevision(LakeTestCase):
    def test_freeze_and_publish(self):
        revision = self.intake()
        anchor = publish_index(self.source, revision.day, revision.number)
        index = load_index(self.source, anchor)
        self.assertEqual((index.day, index.revision), ('2025-06-12', 1))
        posting = next(posting for posting in index.postings if posting.station == '青站')
        self.assertEqual(ordered_members(posting), ('b', 'm', 'z'))
        self.assertEqual(row_details(posting.rows), (
            ('b', '青站', 7, '青站晨册#b'), ('m', '青站', None, '青站晨册#m'), ('z', '青站', 0, '青站晨册#z'),
        ))
        self.assertEqual(sum(row.value for row in posting.rows if row.value is not None), 7)
        self.assertEqual(load_revision(self.source, revision.day, 1), revision)

    def test_duplicate_observation(self):
        original = self.intake()
        day = '2025-06-14'
        with self.assertRaisesRegex(ValueError, 'duplicate observation'):
            intake_batch(self.source, day, [Observation('d', '青站', 7, '原册#1'), Observation('d', '青站', 11, '原册#2')])
        with self.assertRaises(KeyError):
            load_revision(self.source, day, 1)
        self.assertEqual(load_revision(self.source, original.day, 1), original)

    def test_zero_and_missing(self):
        revision = self.intake()
        anchor = publish_index(self.source, revision.day, 1)
        index = load_index(self.source, anchor)
        observed = {row.observation_id: row.value for posting in index.postings for row in posting.rows}
        self.assertEqual(observed, {'b': 7, 'm': None, 'z': 0})
        self.assertIs(type(observed['z']), int)
        self.assertIsNone(observed['m'])

    def test_requested_revision(self):
        (old, newer, _), _ = self.move_history()
        old_anchor = publish_index(self.source, old.day, old.number)
        new_anchor = publish_index(self.source, newer.day, newer.number)
        found = []
        for anchor in (old_anchor, new_anchor):
            index = load_index(self.source, anchor)
            row = next(row for posting in index.postings for row in posting.rows if row.observation_id == 'a')
            found.append((index.revision, row.station, row.value, row.source))
        self.assertEqual(found, [(1, '青站', 7, '青站晨册#a'), (2, '白站', 11, '换站单#a')])
        with self.assertRaises(KeyError):
            publish_index(self.source, old.day, 999)
        self.assertEqual(self.source.connection.execute('SELECT COUNT(*) FROM published_indexes').fetchone()[0], 2)

    def test_repeat_initialize(self):
        (first, second, third), (manifest, _) = self.move_history()
        anchor = publish_index(self.source, first.day, first.number)
        archived = self.issue_body(first.day, anchor, second.number)
        self.source.close()
        with SourceStore(self.source_path) as reopened:
            initialize_source(reopened, ('河口站', '青站'))
            initialize_source(reopened)
            self.assertEqual(load_revision(reopened, first.day, 3), third)
            self.assertEqual(load_index(reopened, anchor).revision, 1)
            self.assertNotIn('河口站', load_index(reopened, anchor).roster.stations)
            self.assertIn('河口站', load_station_roster(reopened).stations)
            self.assertEqual(load_interval_manifest(reopened, first.day, 1, 2), manifest)
            self.assertEqual(load_page(reopened, manifest.entries[0].page_id).changes[0].after.source, '换站单#a')
            self.assertEqual(load_issued(reopened, 1), archived)

    def test_invalid_source(self):
        day = '2025-06-14'
        incoming = [Observation('a', '青站', 7, '青册#a'), Observation('b', '青站', 11, ' '), Observation('c', '青站', 0, '青册#c')]
        with self.assertRaisesRegex(ValueError, 'source reference'):
            intake_batch(self.source, day, incoming)
        with self.assertRaises(KeyError):
            load_revision(self.source, day, 1)
        with self.assertRaisesRegex(ValueError, 'integer'):
            intake_batch(self.source, day, [Observation('b', '青站', True, '青册#b')])
        original = intake_batch(self.source, day, incoming[:1])
        with self.assertRaisesRegex(ValueError, 'already exists'):
            intake_batch(self.source, day, incoming[:1])
        self.assertEqual(load_revision(self.source, day, 1), original)

    def test_explicit_historical_parent_and_integer_precision(self):
        initial = self.intake()
        huge = -(1 << 18000)
        second = freeze_amendment(self.source, initial.day, 1, [Amendment('replace', 'b', Observation('b', '青站', huge, '更正#b'))])
        third = freeze_amendment(self.source, initial.day, 1, [Amendment('delete', 'm')])
        self.assertEqual((second.number, third.number, third.parent), (2, 3, 1))
        values = {row.observation_id: row.value for row in load_revision(self.source, initial.day, 2).observations}
        self.assertTrue(values['b'] == huge)
        self.assertEqual([row.value for row in third.observations], [7, 0])
