import json
from dataclasses import asdict
from pathlib import Path
from tempfile import TemporaryDirectory

from lake.dispatch.archive import initialize_dispatch_archive
from lake.dispatch.issue import issue_handoff
from lake.dispatch.transport import export_issued, read_delivery
from lake.indexes.publish import initialize_indexes, publish_index
from lake.indexes.stations import initialize_station_roster
from lake.pages.history import initialize_page_history, seal_interval
from lake.receiving.accept import accept_handoff
from lake.receiving.lookup import lookup_station
from lake.receiving.store import ReceivingStore
from lake.revisions.amendments import Amendment, freeze_amendment
from lake.revisions.intake import intake_batch
from lake.revisions.observations import Observation
from lake.revisions.store import SourceStore


SAMPLES = Path(__file__).resolve().parent / 'samples'
CODE_PATHS = [
    'lake/revisions/intake.py', 'lake/indexes/publish.py', 'lake/pages/history.py',
    'lake/dispatch/issue.py', 'lake/dispatch/transport.py', 'lake/receiving/accept.py',
    'lake/receiving/lookup.py', 'lake/receiving/totals.py',
]


def read_sample(name):
    return json.loads((SAMPLES / name).read_text(encoding='utf-8'))


def intake(source, sample):
    return intake_batch(source, sample['day'], [Observation(**item) for item in sample['observations']])


def emit_scenario(case_id, inputs, receipts, expected, expected_totals, oracle):
    actual = {}
    totals = {}
    for receipt in receipts:
        actual[receipt.station] = [receipt.status, None if receipt.rows is None else [
            [row.observation_id, row.station, row.value, row.source] for row in receipt.rows
        ]]
        totals[receipt.station] = None if receipt.totals is None else asdict(receipt.totals)
    if actual != expected or totals != expected_totals:
        raise AssertionError(f'{case_id}: expected {(expected, expected_totals)!r}, got {(actual, totals)!r}')
    print('POINTWISE_SCENARIO ' + json.dumps({
        'case_id': case_id, 'input': inputs, 'expected': expected, 'actual': actual,
        'downstream_expected': expected_totals, 'downstream_actual': totals,
        'code_paths': CODE_PATHS, 'oracle': oracle,
    }, ensure_ascii=False, sort_keys=True))


def run(directory):
    ordinary = read_sample('station-day.json')
    moved = read_sample('move-day.json')
    source_path = directory / 'source.sqlite'
    receiving_path = directory / 'receiving.sqlite'
    filenames = {name: directory / f'{name}.handoff' for name in ('equal', 'full', 'partial')}
    with SourceStore(source_path) as source:
        source.initialize()
        initialize_station_roster(source, ordinary['stations'])
        initialize_indexes(source)
        initialize_page_history(source)
        initialize_dispatch_archive(source)
        baseline = intake(source, ordinary)
        anchor = publish_index(source, baseline.day, baseline.number)
        equal = issue_handoff(source, baseline.day, anchor, 1)
        export_issued(source, equal, filenames['equal'])

        before = intake(source, moved)
        anchor = publish_index(source, before.day, before.number)
        first_edits = [Amendment('replace', row['observation_id'], Observation(**row)) for row in moved['corrections'][0]]
        after = freeze_amendment(source, before.day, before.number, first_edits)
        manifest = seal_interval(source, before, after)
        full = issue_handoff(source, before.day, anchor, after.number)
        red_page = next(entry.page_id for entry in manifest.entries if entry.affected_stations == ('红站',))
        partial = issue_handoff(source, before.day, anchor, after.number, [red_page])
        export_issued(source, full, filenames['full'])
        export_issued(source, partial, filenames['partial'])
    source_path.unlink()

    with ReceivingStore(receiving_path) as receiving:
        receiving.initialize()
        identifiers = {name: accept_handoff(receiving, read_delivery(path)) for name, path in filenames.items()}
    with ReceivingStore(receiving_path) as reopened:
        reopened.initialize()
        emit_scenario('V5', ordinary, [lookup_station(reopened, identifiers['equal'], '青站')],
                      {'青站': ['complete', [['b', '青站', 7, '青站晨册#b'], ['m', '青站', None, '青站晨册#m'], ['z', '青站', 0, '青站晨册#z']]]},
                      {'青站': {'row_count': 3, 'missing_count': 1, 'integer_sum': 7}},
                      '站册按 b、m、z 字符顺序核对。整数 7 与 0 求和。')
        emit_scenario('V8', {'day': moved['day'], 'anchor_revision': 1, 'target_revision': 2,
                             'observations': moved['observations'], 'corrections': moved['corrections'][0],
                             'delivered_pages': [entry.page_id for entry in manifest.entries]},
                      [lookup_station(reopened, identifiers['full'], station) for station in ('青站', '白站')],
                      {'青站': ['complete', []], '白站': ['complete', [['a', '白站', 11, '换站单#a']]]},
                      {'青站': {'row_count': 0, 'missing_count': 0, 'integer_sum': 0},
                       '白站': {'row_count': 1, 'missing_count': 0, 'integer_sum': 11}},
                      '更正单明确将唯一青站行 a 改归白站并记为 11。')
        emit_scenario('V9.red_correction', {'day': moved['day'], 'anchor_revision': 1, 'target_revision': 2,
                                            'observations': moved['observations'], 'corrections': moved['corrections'][0],
                                            'delivered_pages': [red_page]},
                      [lookup_station(reopened, identifiers['partial'], station) for station in ('青站', '白站', '红站', '空站')],
                      {'青站': ['insufficient', None], '白站': ['insufficient', None],
                       '红站': ['complete', [['r', '红站', 5, '红站更正#r']]], '空站': ['complete', []]},
                      {'青站': None, '白站': None, '红站': {'row_count': 1, 'missing_count': 0, 'integer_sum': 5},
                       '空站': {'row_count': 0, 'missing_count': 0, 'integer_sum': 0}},
                      '实交红站改值。未交页涉及青白两站。初版和更正单均无空站观测。')


if __name__ == '__main__':
    with TemporaryDirectory() as work:
        run(Path(work))
