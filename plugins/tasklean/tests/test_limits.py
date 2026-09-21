# SPDX-License-Identifier: GPL-3.0-or-later
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from tasklean.limits import LimitsReader, LimitsUnavailable, normalize


def window(used=30, minutes=10080, reset=2000000000):
    return {'usedPercent': used, 'windowDurationMins': minutes, 'resetsAt': reset}


class LimitsTests(unittest.TestCase):
    def test_multiple_buckets_prefer_new_api_and_keep_reported_window_length(self):
        buckets=normalize({'rateLimits': {'primary': window(99)}, 'rateLimitsByLimitId': {
            'codex': {'primary': window(10), 'secondary': None},
            'reserve': {'limitName':'Reserve','primary':window(0,300)}}})
        self.assertEqual(len(buckets),2)
        self.assertEqual(buckets[0]['windows'][0]['remaining_percent'],90)
        self.assertEqual(buckets[0]['windows'][0]['window_minutes'],10080)
        self.assertEqual(buckets[1]['windows'][0]['window_minutes'],300)

    def test_bucket_order_is_stable_when_server_order_changes(self):
        buckets = {'gpt-reserve': {'primary': window(0)}, 'codex': {'primary': window(25)},
                   'another': {'primary': window(50)}}
        first = normalize({'rateLimitsByLimitId': buckets})
        second = normalize({'rateLimitsByLimitId': dict(reversed(list(buckets.items())))})
        self.assertEqual(first, second)
        self.assertEqual(first[0]['id'], 'codex')

    def test_model_catalog_paginates_filters_and_reuses_quota_connection(self):
        reader = LimitsReader(); self.addCleanup(reader.close)
        model = {'model': 'example', 'displayName': 'Example',
                 'supportedReasoningEfforts': [{'reasoningEffort': 'low'}, {'reasoningEffort': 'ultra'}],
                 'defaultReasoningEffort': 'low', 'privateExtra': 'SECRET'}
        pages = [{'data': [model, {'model': 'hidden', 'hidden': True}], 'nextCursor': 'page2'},
                 {'data': [model, {'model': 'second'}], 'nextCursor': None}]
        with patch.object(reader, '_ensure_started') as start, patch.object(reader, '_request', side_effect=pages) as request:
            result = reader.models()
            self.assertTrue(result['available'])
            self.assertEqual([m['model'] for m in result['models']], ['example', 'second'])
            self.assertEqual(result['models'][0]['reasoning_efforts'], ['low', 'ultra'])
            self.assertNotIn('SECRET', json.dumps(result))
            self.assertEqual(request.call_args.args, ('model/list', {'limit': 100, 'includeHidden': False, 'cursor': 'page2'}))
            reader.models(); reader.models(force=True)
            self.assertEqual(request.call_count, 2)
            start.assert_called_once()
        reader.model_attempted = None
        with patch.object(reader, '_read_models', side_effect=LimitsUnavailable('SECRET')):
            stale = reader.models()
            self.assertTrue(stale['stale'])
            self.assertEqual(stale['models'], result['models'])
            self.assertNotIn('SECRET', json.dumps(stale))

    def test_model_catalog_repeated_cursor_and_bad_response_are_unavailable(self):
        for page in ({'data': [], 'nextCursor': 'same'}, {'data': None}):
            reader = LimitsReader(); self.addCleanup(reader.close)
            with patch.object(reader, '_ensure_started'), patch.object(reader, '_request', return_value=page):
                self.assertFalse(reader.models()['available'])

    def test_legacy_null_unknown_and_clamp(self):
        buckets=normalize({'rateLimits':{'primary':window(120),'secondary':window(None,None,None)}})
        self.assertEqual(buckets[0]['windows'][0]['remaining_percent'],0)
        self.assertIsNone(buckets[0]['windows'][1]['remaining_percent'])
        self.assertIsNone(buckets[0]['windows'][1]['resets_at'])
        self.assertEqual(normalize({'rateLimits':{'primary':window(-5)}})[0]['windows'][0]['remaining_percent'],100)
        for used in (True, '10', float('nan'), float('inf'), 10**400):
            self.assertIsNone(normalize({'rateLimits':{'primary':window(used)}})[0]['windows'][0]['remaining_percent'])
        for raw in ({}, None, {'rateLimits':None}, {'rateLimitsByLimitId':{'codex':{}}}):
            with self.assertRaises(LimitsUnavailable): normalize(raw)

    def test_cache_force_throttle_and_stale_failure(self):
        reader=LimitsReader()
        self.addCleanup(reader.close)
        with patch.object(reader,'_read',return_value=[{'id':'codex','windows':[]}]) as read, patch('tasklean.limits.time.monotonic',return_value=100):
            first=reader.read()
            self.assertTrue(first['available'])
            reader.read();reader.read(force=True)
            self.assertEqual(read.call_count,1)
        with patch.object(reader,'_read',side_effect=LimitsUnavailable()), patch('tasklean.limits.time.monotonic',return_value=161):
            failed=reader.read()
            self.assertTrue(failed['stale'])
            self.assertEqual(failed['checked_at'],first['checked_at'])
            self.assertEqual(failed['buckets'],first['buckets'])

    def test_missing_login_or_cli_is_unknown_not_zero(self):
        reader=LimitsReader('/no/such/codex')
        self.addCleanup(reader.close)
        result=reader.read()
        self.assertFalse(result['available'])
        self.assertEqual(result['buckets'],[])
        self.assertIsNone(result['checked_at'])

    def fixture(self, behavior):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        path=Path(temp.name)/'codex'
        path.write_text('#!'+sys.executable+'\n'+behavior)
        path.chmod(0o700)
        return str(path)

    def test_stdio_handshake_persistent_process_and_read_only_methods(self):
        binary=self.fixture('''import json,sys
initialized=False
for line in sys.stdin:
 r=json.loads(line);method=r.get('method')
 if method=='initialize':
  assert not initialized
  print(json.dumps({'id':r['id'],'result':{}}),flush=True)
 elif method=='initialized': initialized=True
 elif method=='model/list':
  assert initialized
  print(json.dumps({'id':r['id'],'result':{'data':[{'model':'example'}],'nextCursor':None}}),flush=True)
 elif method=='account/rateLimits/read':
  assert initialized
  print(json.dumps({'method':'account/rateLimits/updated','params':{}}),flush=True)
  print(json.dumps({'id':r['id'],'result':{'rateLimits':{'primary':{'usedPercent':25,'windowDurationMins':300,'resetsAt':2000000000}}}}),flush=True)
 else: raise RuntimeError('Unexpected method')
''')
        reader=LimitsReader(binary,timeout=1);self.addCleanup(reader.close)
        first=reader.read();self.assertTrue(first['available'])
        pid=reader.proc.pid
        self.assertEqual(reader.models()['models'][0]['model'], 'example')
        self.assertEqual(reader.proc.pid, pid)
        reader.attempted=None
        again=reader.read()
        self.assertEqual(reader.proc.pid,pid)
        self.assertEqual(again['buckets'][0]['windows'][0]['remaining_percent'],75)
        reader.close();self.assertIsNone(reader.proc)

    def test_timeout_and_private_error_never_reach_browser(self):
        binary=self.fixture('''import json,sys
for line in sys.stdin:
 r=json.loads(line)
 if 'id' in r: print(json.dumps({'id':r['id'],'error':{'message':'SECRET fixture auth detail'}}),flush=True)
''')
        reader=LimitsReader(binary,timeout=.1);self.addCleanup(reader.close)
        result=reader.read()
        self.assertFalse(result['available'])
        self.assertNotIn('SECRET',json.dumps(result))
        hanging=self.fixture('import time\ntime.sleep(10)\n')
        reader=LimitsReader(hanging,timeout=.1);self.addCleanup(reader.close)
        self.assertFalse(reader.read()['available'])
        self.assertIsNone(reader.proc)
