# SPDX-License-Identifier: GPL-3.0-or-later
import http.client
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from unittest.mock import patch, MagicMock
from urllib.error import HTTPError, URLError
from uuid import uuid4

from tasklean import __version__
from tasklean.feedback import submit_feedback, validate_feedback, NoRedirect, FEEDBACK_URL
from tasklean.feedback_service import FeedbackStore, make_server


def payload():
    return {'submission_id': str(uuid4()), 'message': 'The project groups are useful.',
            'rating': 4, 'email': '', 'app_version': __version__}


def form():
    data = payload()
    data.pop('app_version')
    return {**data, 'consent': True}


class FeedbackClientTests(unittest.TestCase):
    def test_no_consent_or_extra_fields_never_sends(self):
        with patch('tasklean.feedback.build_opener') as opener:
            for data in [form() | {'consent': False}, form() | {'task_log': 'private'}, {}]:
                with self.assertRaises(ValueError): submit_feedback(data)
            opener.assert_not_called()

    def test_only_explicit_fields_sent_with_version_and_verified_destination(self):
        data = form()
        response = MagicMock()
        response.__enter__.return_value = response
        response.status = 201
        response.read.return_value = json.dumps({'received': True, 'submission_id': data['submission_id']}).encode()
        with patch('tasklean.feedback.build_opener') as opener:
            opener.return_value.open.return_value = response
            self.assertTrue(submit_feedback(data)['received'])
            request = opener.return_value.open.call_args.args[0]
            self.assertEqual(request.full_url, FEEDBACK_URL)
            sent = json.loads(request.data)
            self.assertEqual(set(sent), {'submission_id', 'message', 'rating', 'email', 'app_version'})
            self.assertEqual(sent['app_version'], __version__)
            self.assertIsInstance(opener.call_args.args[0], NoRedirect)

    def test_failed_unacknowledged_or_limited_submission_is_not_success(self):
        for failure in [URLError('network'), HTTPError(FEEDBACK_URL, 429, 'limited', {}, None), HTTPError(FEEDBACK_URL, 302, 'redirect', {}, None)]:
            with patch('tasklean.feedback.build_opener') as opener:
                opener.return_value.open.side_effect = failure
                with self.assertRaises(ValueError): submit_feedback(form())
        response = MagicMock()
        response.__enter__.return_value = response
        response.status = 200
        response.read.return_value = b'{"received":true,"submission_id":"wrong"}'
        with patch('tasklean.feedback.build_opener') as opener:
            opener.return_value.open.return_value = response
            with self.assertRaises(ValueError): submit_feedback(form())

    def test_schema_rejects_large_invalid_and_unknown_data(self):
        for update in [{'message': ' '}, {'message': 'x'*4001}, {'rating': True}, {'rating': 6}, {'email': 'bad\naddress'}, {'app_version': '<script>'}, {'submission_id': []}, {'logs': 'secret'}]:
            with self.assertRaises(ValueError): validate_feedback(payload() | update)
        self.assertIsNone(validate_feedback(payload() | {'rating': None})['rating'])


class FeedbackReceiverTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.database = Path(self.temp.name) / 'private' / 'feedback.sqlite3'
        self.server = make_server(self.database, port=0)
        self.worker = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.worker.start()
        self.addCleanup(self.stop)

    def stop(self):
        self.server.shutdown(); self.server.server_close(); self.worker.join()

    def request(self, data=None, path='/api/feedback', content_type='application/json'):
        conn = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=5)
        try:
            conn.request('POST' if data is not None else 'GET', path, json.dumps(data) if data is not None else None, {'Content-Type': content_type})
            response = conn.getresponse()
            return response.status, json.loads(response.read())
        finally: conn.close()

    def test_explicit_submission_persists_and_duplicate_is_safe(self):
        data = payload()
        self.assertEqual(self.request(data)[0], 201)
        self.assertEqual(self.request(data)[0], 200)
        rows = FeedbackStore(self.database).recent()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['message'], data['message'])
        self.assertEqual(set(rows[0]), set(data) | {'created_at'})
        self.assertEqual(self.database.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.database.parent.stat().st_mode & 0o777, 0o700)

    def test_changed_content_cannot_reuse_receipt(self):
        data = payload()
        self.assertEqual(self.request(data)[0], 201)
        self.assertEqual(self.request(data | {'message': 'different'})[0], 400)
        self.assertEqual(self.server.store.recent()[0]['message'], data['message'])

    def test_no_public_read_and_invalid_requests_not_stored(self):
        self.assertEqual(self.request()[0], 404)
        self.assertEqual(self.request(path='/healthz')[0], 200)
        self.assertEqual(self.request(payload(), path='/api/tasks')[0], 404)
        self.assertEqual(self.request(payload(), content_type='text/plain')[0], 415)
        self.assertEqual(self.request({'prompt': 'private'})[0], 400)
        self.assertEqual(self.request(payload() | {'message': 'x'*40000})[0], 413)
        self.assertEqual(self.server.store.recent(), [])

    def test_storage_failure_never_reports_received(self):
        with patch.object(self.server.store, 'record', side_effect=sqlite3.OperationalError('disk error')):
            status, body = self.request(payload())
        self.assertEqual(status, 503)
        self.assertNotIn('received', body)


if __name__ == '__main__': unittest.main()
