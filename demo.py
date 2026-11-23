import json
from dataclasses import asdict
from pathlib import Path
from tempfile import TemporaryDirectory

from lake.dispatch.archive import initialize_dispatch_archive
from lake.dispatch.envelope import decode_envelope
from lake.dispatch.issue import issue_handoff, issue_joint_proof
from lake.dispatch.transport import export_issued, read_delivery
from lake.indexes.publish import initialize_indexes, publish_index
from lake.indexes.stations import initialize_station_roster
from lake.pages.history import initialize_page_history, seal_interval
from lake.receiving.accept import accept_handoff
from lake.receiving.lookup import lookup_joint_station, lookup_station
from lake.receiving.store import ReceivingStore
from lake.revisions.amendments import Amendment, freeze_amendment
from lake.revisions.intake import intake_batch
from lake.revisions.observations import Observation
from lake.revisions.store import SourceStore


SAMPLES = Path(__file__).resolve().parent / 'samples'
CODE_PATHS = [
    'lake/revisions/intake.py', 'lake/indexes/publish.py', 'lake/pages/history.py',
    'lake/dispatch/issue.py', 'lake/dispatch/joint_proof.py', 'lake/dispatch/transport.py',
    'lake/receiving/accept.py', 'lake/receiving/lookup.py', 'lake/receiving/joint.py',
    'lake/receiving/totals.py',
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


def evidence_row(receiving, evidence, observation_id):
    """Re-fetch the reported original row at its evidence position."""
    envelope = decode_envelope(receiving.read_accepted(evidence.receipt_id))
    if evidence.side == 'anchor':
        rows = (row for posting in envelope.anchor.postings for row in posting.rows)
        return next(row for row in rows if row.observation_id == observation_id)
    page = next(page for page in envelope.delivered_pages if page.page_id == evidence.page_id)
    change = next(change for change in page.changes if change.observation_id == observation_id)
    return change.before if evidence.side == 'before' else change.after


def emit_joint_scenario(case_id, inputs, result, expected_rows, expected_totals,
                        expected_evidence, expected_missing, oracle):
    actual_rows = None if result.rows is None else [
        [row.observation_id, row.station, row.value, row.source] for row in result.rows
    ]
    actual_totals = None if result.totals is None else asdict(result.totals)
    actual_evidence = None if result.evidence is None else [
        {'receipt_id': item.receipt_id, 'revision': item.revision,
         'page_id': item.page_id, 'side': item.side}
        for item in result.evidence
    ]
    actual = {
        'status': result.status, 'rows': actual_rows, 'totals': actual_totals,
        'evidence': actual_evidence, 'missing_observation_ids': list(result.missing_observation_ids),
    }
    expected = {
        'status': 'complete' if expected_rows is not None else 'insufficient',
        'rows': expected_rows, 'totals': expected_totals,
        'evidence': expected_evidence,
        'missing_observation_ids': expected_missing,
    }
    if actual != expected:
        raise AssertionError(f'{case_id}: expected {expected!r}, got {actual!r}')
    print('POINTWISE_SCENARIO ' + json.dumps({
        'case_id': case_id, 'input': inputs, 'expected': expected, 'actual': actual,
        'code_paths': CODE_PATHS, 'oracle': oracle,
    }, ensure_ascii=False, sort_keys=True))


def run_joint_demo(directory):
    """Dual-anchor joint reconciliation, disconnected from the source store."""
    day = '2025-06-17'
    station = '青站'
    source_path = directory / 'joint-source.sqlite'
    receiving_path = directory / 'joint-receiving.sqlite'

    def seal_chain(source, versions):
        for before, after in zip(versions, versions[1:]):
            seal_interval(source, before, after)

    with SourceStore(source_path) as source:
        source.initialize()
        initialize_station_roster(source, (station, '空站'))
        initialize_indexes(source)
        initialize_page_history(source)
        initialize_dispatch_archive(source)

        # Scenario J1: the fixture of JOINT_LOOKUP.md, two anchor-only pieces.
        v1 = intake_batch(source, day, (
            Observation('a', station, 7, '晨册#a'),
            Observation('b', station, 11, '晨册#b'),
        ))
        v2 = freeze_amendment(source, day, 1, (
            Amendment('replace', 'a', Observation('a', station, 8, '午勘#a')),
        ))
        v3 = freeze_amendment(source, day, 2, (
            Amendment('replace', 'b', Observation('b', station, 12, '晚勘#b')),
        ))
        seal_chain(source, (v1, v2, v3))
        j1_early = issue_handoff(source, day, publish_index(source, day, 1), 2, ())
        j1_late = issue_handoff(source, day, publish_index(source, day, 3), 2, ())
        j1_proof = issue_joint_proof(source, j1_early, j1_late)

        # Scenario J2: source-only change plus an added missing value, no pages.
        day_two = '2025-06-18'
        w1 = intake_batch(source, day_two, (Observation('a', station, 7, '晨册#a'),))
        w2 = freeze_amendment(source, day_two, 1, (
            Amendment('replace', 'a', Observation('a', station, 7, '复测#a')),
            Amendment('add', 'm', Observation('m', station, None, '补测#m')),
        ))
        w3 = freeze_amendment(source, day_two, 2, (
            Amendment('replace', 'a', Observation('a', station, 7, '晨册#a')),
            Amendment('delete', 'm'),
        ))
        seal_chain(source, (w1, w2, w3))
        j2_early = issue_handoff(source, day_two, publish_index(source, day_two, 1), 2, ())
        j2_late = issue_handoff(source, day_two, publish_index(source, day_two, 3), 2, ())
        j2_proof = issue_joint_proof(source, j2_early, j2_late)
        # A second late piece actually carrying both 2->3 correction pages.
        from lake.pages.history import load_interval_manifest
        manifest_23 = load_interval_manifest(source, day_two, 2, 3)
        j2_late_full = issue_handoff(
            source, day_two, publish_index(source, day_two, 3), 2,
            [entry.page_id for entry in manifest_23.entries],
        )
        j2_full_proof = issue_joint_proof(source, j2_early, j2_late_full)

        # Scenario J3: absent, restored, absent again across four revisions.
        day_three = '2025-06-19'
        x1 = intake_batch(source, day_three, (Observation('a', station, 7, '晨册#a'),))
        x2 = freeze_amendment(source, day_three, 1, (Amendment('delete', 'a'),))
        x3 = freeze_amendment(source, day_three, 2, (
            Amendment('add', 'a', Observation('a', station, 7, '晨册#a')),
        ))
        x4 = freeze_amendment(source, day_three, 3, (Amendment('delete', 'a'),))
        seal_chain(source, (x1, x2, x3, x4))
        j3_early = issue_handoff(
            source, day_three, publish_index(source, day_three, 1), 3,
            [f'{day_three}/1-2/1', f'{day_three}/2-3/1'],
        )
        j3_late = issue_handoff(source, day_three, publish_index(source, day_three, 4), 3, ())
        j3_proof = issue_joint_proof(source, j3_early, j3_late)

        # Ship every accepted body to files before the source is removed.
        shipments = {
            'j1_early': j1_early, 'j1_late': j1_late,
            'j2_early': j2_early, 'j2_late': j2_late, 'j2_late_full': j2_late_full,
            'j3_early': j3_early, 'j3_late': j3_late,
        }
        shipment_paths = {
            name: directory / f'{name}.handoff' for name in shipments
        }
        for name, handoff_id in shipments.items():
            export_issued(source, handoff_id, shipment_paths[name])

    source_path.unlink()

    with ReceivingStore(receiving_path) as receiving:
        receiving.initialize()
        j1_receipts = (
            accept_handoff(receiving, read_delivery(shipment_paths['j1_early'])),
            accept_handoff(receiving, read_delivery(shipment_paths['j1_late'])),
        )
        j2_early_receipt = accept_handoff(receiving, read_delivery(shipment_paths['j2_early']))
        j2_late_receipt = accept_handoff(receiving, read_delivery(shipment_paths['j2_late']))
        j2_full_late_receipt = accept_handoff(receiving, read_delivery(shipment_paths['j2_late_full']))
        j3_receipts = (
            accept_handoff(receiving, read_delivery(shipment_paths['j3_early'])),
            accept_handoff(receiving, read_delivery(shipment_paths['j3_late'])),
        )

    # Re-open the isolated store after the source is gone: identical results.
    with ReceivingStore(receiving_path) as receiving:
        result = lookup_joint_station(receiving, j1_proof, *j1_receipts, station)
        for row, item in zip(result.rows, result.evidence):
            if evidence_row(receiving, item, row.observation_id) != row:
                raise AssertionError('J1 evidence position does not return its original row')
        emit_joint_scenario(
            'J1.dual_anchor_19',
            {'day': day, 'target_revision': 2, 'early_anchor': 1, 'late_anchor': 3, 'delivered_pages': []},
            result,
            [['a', station, 8, '午勘#a'], ['b', station, 11, '晨册#b']],
            {'row_count': 2, 'missing_count': 0, 'integer_sum': 19},
            [
                {'receipt_id': j1_receipts[1], 'revision': 3, 'page_id': None, 'side': 'anchor'},
                {'receipt_id': j1_receipts[0], 'revision': 1, 'page_id': None, 'side': 'anchor'},
            ],
            [],
            '晚件第3版锚点的 a 与早件第1版锚点的 b 均连续到第2版，合计 8+11=19。',
        )
        empty = lookup_joint_station(receiving, j1_proof, *j1_receipts, '空站')
        emit_joint_scenario(
            'J1.empty_station_zero',
            {'day': day, 'target_revision': 2, 'station': '空站'},
            empty, [], {'row_count': 0, 'missing_count': 0, 'integer_sum': 0}, [], [],
            '目标版无空站成员，确证空账出零明细零计量。',
        )

        unproven = lookup_joint_station(
            receiving, j2_proof, j2_early_receipt, j2_late_receipt, station,
        )
        emit_joint_scenario(
            'J2.no_pages_both_missing',
            {'day': day_two, 'target_revision': 2, 'delivered_pages': []},
            unproven, None, None, None, ['a', 'm'],
            'a 仅改出处、m 为新增缺测；无页件无法连续取证，两行均欠证且不出明细合计。',
        )
        if unproven.missing_observation_ids != ('a', 'm'):
            raise AssertionError('J2 missing observation mismatch')

        proven = lookup_joint_station(
            receiving, j2_full_proof, j2_early_receipt, j2_full_late_receipt, station,
        )
        for row, item in zip(proven.rows, proven.evidence):
            if evidence_row(receiving, item, row.observation_id) != row:
                raise AssertionError('J2 evidence position does not return its original row')
        emit_joint_scenario(
            'J2.before_images_prove',
            {'day': day_two, 'target_revision': 2,
             'late_delivered_pages': [f'{day_two}/2-3/1', f'{day_two}/2-3/2']},
            proven,
            [['a', station, 7, '复测#a'], ['m', station, None, '补测#m']],
            {'row_count': 2, 'missing_count': 1, 'integer_sum': 7},
            [
                {'receipt_id': j2_full_late_receipt, 'revision': 2,
                 'page_id': f'{day_two}/2-3/1', 'side': 'before'},
                {'receipt_id': j2_full_late_receipt, 'revision': 2,
                 'page_id': f'{day_two}/2-3/2', 'side': 'before'},
            ],
            [],
            '两张第2→3版更正页的前像给出第2版原行：a 为复测出处，m 缺测，合计 7。',
        )

        restored = lookup_joint_station(receiving, j3_proof, *j3_receipts, station)
        if evidence_row(receiving, restored.evidence[0], 'a') != restored.rows[0]:
            raise AssertionError('J3 evidence position does not return its original row')
        emit_joint_scenario(
            'J3.delete_readd_delete',
            {'day': day_three, 'target_revision': 3, 'early_anchor': 1, 'late_anchor': 4},
            restored,
            [['a', station, 7, '晨册#a']],
            {'row_count': 1, 'missing_count': 0, 'integer_sum': 7},
            [{'receipt_id': j3_receipts[0], 'revision': 3,
              'page_id': f'{day_three}/2-3/1', 'side': 'after'}],
            [],
            '第1→2版前像在目标前已断档；仅第2→3版后像证明第3版恢复的 a。',
        )


if __name__ == '__main__':
    with TemporaryDirectory() as work:
        run(Path(work))
        run_joint_demo(Path(work))
