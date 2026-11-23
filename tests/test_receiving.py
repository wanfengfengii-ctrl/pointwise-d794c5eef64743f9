import json
import sqlite3

from lake.indexes.publish import publish_index
from lake.receiving.accept import accept_handoff
from lake.receiving.lookup import lookup_station
from tests.support import LakeTestCase, row_details


class TestReceiving(LakeTestCase):
    def moved_body(self):
        (old, middle, _), _ = self.move_history()
        anchor = publish_index(self.source, old.day, 1)
        return self.issue_body(old.day, anchor, middle.number)

    def install_commit_gate(self, receiving):
        receiving.connection.executescript('''
            CREATE TABLE test_gate_parent (token INTEGER PRIMARY KEY);
            CREATE TABLE test_gate_child (
                receipt_id INTEGER PRIMARY KEY,
                token INTEGER NOT NULL REFERENCES test_gate_parent(token) DEFERRABLE INITIALLY DEFERRED
            );
            CREATE TRIGGER test_gate_after_accept AFTER INSERT ON accepted_handoffs
            BEGIN
                INSERT INTO test_gate_child (receipt_id, token) VALUES (NEW.receipt_id, 1);
            END;
        ''')

    def test_page_manifest_mismatch(self):
        body = self.moved_body()
        receiving = self.receiver()
        data = json.loads(body)
        data['delivered_pages'][0]['changes'][0]['after']['value'] = '63'
        with self.assertRaisesRegex(ValueError, 'fingerprint'):
            accept_handoff(receiving, json.dumps(data).encode())
        self.assertEqual(receiving.connection.execute('SELECT COUNT(*) FROM accepted_handoffs').fetchone()[0], 0)
        accepted = accept_handoff(receiving, body)
        self.assertEqual(receiving.read_accepted(accepted), body)

    def test_manifest_scope_and_page_identity_mismatch(self):
        body = self.moved_body()
        receiving = self.receiver()
        data = json.loads(body)
        data['manifests'][0]['entries'][0]['affected_stations'] = ['白站']
        with self.assertRaisesRegex(ValueError, 'affected stations'):
            accept_handoff(receiving, json.dumps(data).encode())
        data = json.loads(body)
        data['manifests'] = []
        with self.assertRaisesRegex(ValueError, 'manifest chain'):
            accept_handoff(receiving, json.dumps(data).encode())
        data = json.loads(body)
        data['delivered_pages'].append(data['delivered_pages'][0])
        with self.assertRaisesRegex(ValueError, 'repeated delivered page'):
            accept_handoff(receiving, json.dumps(data).encode())
        with self.assertRaises(KeyError):
            receiving.read_accepted(1)

    def test_valid_handoff(self):
        body = self.moved_body()
        data = json.loads(body)
        data['delivered_pages'].pop(0)
        partial = json.dumps(data, ensure_ascii=False, indent=2).encode('utf-8')
        receiving = self.receiver()
        full_id = accept_handoff(receiving, body)
        partial_id = accept_handoff(receiving, partial)
        self.assertEqual(receiving.read_accepted(full_id), body)
        self.assertEqual(receiving.read_accepted(partial_id), partial)
        self.assertEqual(lookup_station(receiving, full_id, '白站').status, 'complete')
        self.assertEqual(lookup_station(receiving, partial_id, '白站').status, 'insufficient')
        self.assertEqual(lookup_station(receiving, partial_id, '红站').totals.integer_sum, 5)

    def test_commit_failure(self):
        body = self.moved_body()
        receiving = self.receiver()
        first = accept_handoff(receiving, body)
        self.install_commit_gate(receiving)
        with self.assertRaisesRegex(sqlite3.IntegrityError, 'FOREIGN KEY constraint failed'):
            accept_handoff(receiving, body)
        self.assertFalse(receiving.connection.in_transaction)
        receiving.close()
        reopened = self.receiver()
        self.assertEqual(reopened.read_accepted(first), body)
        self.assertEqual(reopened.connection.execute('SELECT receipt_id FROM accepted_handoffs').fetchall(), [(first,)])
        self.assertEqual(reopened.connection.execute('SELECT COUNT(*) FROM test_gate_child').fetchone()[0], 0)
        with self.assertRaises(KeyError):
            lookup_station(reopened, first + 1, '白站')

    def test_retry_after_failure(self):
        body = self.moved_body()
        receiving = self.receiver()
        first = accept_handoff(receiving, body)
        self.install_commit_gate(receiving)
        with self.assertRaises(sqlite3.IntegrityError):
            accept_handoff(receiving, body)
        receiving.close()
        reopened = self.receiver()
        self.assertEqual(reopened.connection.execute('SELECT COUNT(*) FROM accepted_handoffs').fetchone()[0], 1)
        reopened.connection.execute('DROP TRIGGER test_gate_after_accept')
        retried = accept_handoff(reopened, body)
        self.assertNotEqual(first, retried)
        self.assertEqual(reopened.read_accepted(first), reopened.read_accepted(retried))
        self.assertEqual(row_details(lookup_station(reopened, retried, '白站').rows), (('a', '白站', 11, '换站单#a'),))
