import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from lake.dispatch.archive import initialize_dispatch_archive, load_issued
from lake.dispatch.issue import issue_handoff
from lake.indexes.publish import initialize_indexes
from lake.indexes.stations import initialize_station_roster
from lake.pages.history import initialize_page_history, seal_interval
from lake.receiving.store import ReceivingStore
from lake.revisions.amendments import Amendment, freeze_amendment
from lake.revisions.intake import intake_batch
from lake.revisions.observations import Observation
from lake.revisions.store import SourceStore


SAMPLES = Path(__file__).resolve().parent.parent / 'samples'


def fixture(filename):
    return json.loads((SAMPLES / filename).read_text(encoding='utf-8'))


def initialize_source(store, stations=()):
    store.initialize()
    initialize_station_roster(store, stations)
    initialize_indexes(store)
    initialize_page_history(store)
    initialize_dispatch_archive(store)


def row_details(rows):
    return tuple((row.observation_id, row.station, row.value, row.source) for row in rows)


class LakeTestCase(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.directory = Path(directory.name)
        self.source_path = self.directory / 'source.sqlite'
        self.receiving_path = self.directory / 'receiving.sqlite'
        self.source = SourceStore(self.source_path)
        self.addCleanup(self.source.close)
        initialize_source(self.source, fixture('station-day.json')['stations'])

    def receiver(self):
        receiving = ReceivingStore(self.receiving_path)
        self.addCleanup(receiving.close)
        receiving.initialize()
        return receiving

    def intake(self, filename='station-day.json'):
        sample = fixture(filename)
        return intake_batch(self.source, sample['day'], map(lambda row: Observation(**row), sample['observations']))

    def move_history(self):
        base = self.intake('move-day.json')
        versions = [base]
        manifests = []
        for corrections in fixture('move-day.json')['corrections']:
            before = versions[-1]
            edits = [Amendment('replace', item['observation_id'], Observation(**item)) for item in corrections]
            after = freeze_amendment(self.source, base.day, before.number, edits)
            manifests.append(seal_interval(self.source, before, after))
            versions.append(after)
        return tuple(versions), tuple(manifests)

    def issue_body(self, day, anchor, target, pages=None):
        identifier = issue_handoff(self.source, day, anchor, target, pages)
        return load_issued(self.source, identifier)

    def disconnect_source(self):
        self.source.close()
        self.source_path.unlink()
