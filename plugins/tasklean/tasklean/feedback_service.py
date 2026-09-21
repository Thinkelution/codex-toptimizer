# SPDX-License-Identifier: GPL-3.0-or-later
"""Private feedback store behind Nginx. No public read endpoint."""
import argparse
import json
import os
import sqlite3
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .feedback import validate_feedback


class FeedbackStore:
    def __init__(self, database):
        self.database = Path(database)
        self.database.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        with sqlite3.connect(self.database) as db:
            db.execute('CREATE TABLE IF NOT EXISTS feedback (submission_id TEXT PRIMARY KEY, created_at TEXT NOT NULL, message TEXT NOT NULL, rating INTEGER, email TEXT NOT NULL, app_version TEXT NOT NULL)')
        os.chmod(self.database, 0o600)

    def record(self, data):
        item = validate_feedback(data)
        with sqlite3.connect(self.database, timeout=5) as db:
            db.execute('BEGIN IMMEDIATE')
            previous = db.execute('SELECT message, rating, email, app_version FROM feedback WHERE submission_id = ?', (item['submission_id'],)).fetchone()
            content = tuple(item[k] for k in ('message', 'rating', 'email', 'app_version'))
            if previous is not None:
                if previous != content:
                    raise ValueError('Submission ID already used for different feedback')
                return False
            if db.execute('SELECT count(*) FROM feedback').fetchone()[0] >= 100000:
                raise OSError('Feedback storage full')
            db.execute('INSERT INTO feedback VALUES (?, ?, ?, ?, ?, ?)',
                       (item['submission_id'], datetime.now(timezone.utc).isoformat(), *content))
        return True

    def recent(self, limit=20):
        with sqlite3.connect(self.database) as db:
            db.row_factory = sqlite3.Row
            return [dict(row) for row in db.execute('SELECT * FROM feedback ORDER BY created_at DESC LIMIT ?', (limit,))]


def make_server(database, port=8790):
    store = FeedbackStore(database)
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def reply(self, status, body):
            data = json.dumps(body).encode()
            self.send_response(status)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(data)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            self.reply(200, {'ok': True}) if self.path == '/healthz' else self.reply(404, {'error': 'Not found'})

        def do_POST(self):
            if self.path != '/api/feedback':
                return self.reply(404, {'error': 'Not found'})
            if self.headers.get('Content-Type', '').split(';')[0] != 'application/json':
                return self.reply(415, {'error': 'JSON required'})
            if self.headers.get('Transfer-Encoding'):
                return self.reply(400, {'error': 'Content-Length required'})
            try:
                length = int(self.headers.get('Content-Length', '0'))
                if not 1 <= length <= 32768:
                    return self.reply(413, {'error': 'Request too large or empty'})
                item = validate_feedback(json.loads(self.rfile.read(length)))
                inserted = store.record(item)
            except (ValueError, TypeError, UnicodeError):
                return self.reply(400, {'error': 'Invalid feedback'})
            except (OSError, sqlite3.Error):
                return self.reply(503, {'error': 'Feedback temporarily unavailable'})
            self.reply(201 if inserted else 200, {'received': True, 'submission_id': item['submission_id']})

        def setup(self):
            super().setup()
            self.connection.settimeout(10)

    server = ThreadingHTTPServer(('127.0.0.1', port), Handler)
    server.store = store
    return server


def main():
    parser = argparse.ArgumentParser(description='Private Thinkelution feedback receiver and inbox')
    parser.add_argument('command', choices=('serve', 'list'))
    parser.add_argument('--database', required=True)
    parser.add_argument('--port', type=int, default=8790)
    parser.add_argument('--limit', type=int, default=20)
    args = parser.parse_args()
    if args.command == 'list':
        if not 1 <= args.limit <= 1000:
            parser.error('--limit must be 1–1000')
        print(json.dumps(FeedbackStore(args.database).recent(args.limit), indent=2))
    else:
        server = make_server(args.database, args.port)
        try:
            server.serve_forever()
        finally:
            server.server_close()


if __name__ == '__main__':
    main()
