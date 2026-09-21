# SPDX-License-Identifier: GPL-3.0-or-later
"""Explicit feedback submission; never reads projects, prompts, logs or credentials."""
import json
import re
import uuid
from urllib.error import HTTPError, URLError
from urllib.request import Request, HTTPRedirectHandler, build_opener

from . import __version__

FEEDBACK_URL = 'https://codex-lean-task.thinkelution.com/api/feedback'


def validate_feedback(data):
    fields = {'submission_id', 'message', 'rating', 'email', 'app_version'}
    if not isinstance(data, dict) or set(data) != fields:
        raise ValueError('Invalid feedback fields')
    try:
        identifier = str(uuid.UUID(data['submission_id']))
    except (ValueError, TypeError, AttributeError):
        raise ValueError('Invalid submission ID') from None
    message, rating, email, version = (data[k] for k in ('message', 'rating', 'email', 'app_version'))
    if not isinstance(message, str) or not 1 <= len(message.strip()) <= 4000:
        raise ValueError('Write a message between 1 and 4,000 characters')
    if rating is not None and (type(rating) is not int or not 1 <= rating <= 5):
        raise ValueError('Choose a rating from 1 to 5')
    if not isinstance(email, str) or len(email) > 254 or (email and not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', email)):
        raise ValueError('Enter a valid email or leave it blank')
    if not isinstance(version, str) or not re.fullmatch(r'[A-Za-z0-9.+_-]{1,50}', version):
        raise ValueError('Invalid app version')
    return {'submission_id': identifier, 'message': message.strip(), 'rating': rating,
            'email': email, 'app_version': version}


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def submit_feedback(data):
    if set(data) != {'submission_id', 'message', 'rating', 'email', 'consent'} or data.get('consent') is not True:
        raise ValueError('Feedback is sent only with your explicit consent')
    payload = validate_feedback({k: data[k] for k in ('submission_id', 'message', 'rating', 'email')} | {'app_version': __version__})
    request = Request(FEEDBACK_URL, data=json.dumps(payload).encode(), method='POST',
                      headers={'Content-Type': 'application/json', 'User-Agent': 'Codex-LeanTask/' + __version__})
    try:
        with build_opener(NoRedirect()).open(request, timeout=12) as response:
            raw = response.read(4097)
            result = json.loads(raw) if len(raw) <= 4096 else None
            if response.status not in (200, 201) or not isinstance(result, dict) or result.get('received') is not True or result.get('submission_id') != payload['submission_id']:
                raise ValueError('Invalid acknowledgment')
    except HTTPError as error:
        if error.code == 429:
            raise ValueError('Too many feedback requests. Wait a minute and try again.') from None
        raise ValueError('Feedback was not confirmed. Your message is still here; retry when ready.') from None
    except (URLError, OSError, TimeoutError, ValueError):
        raise ValueError('Feedback was not confirmed. Your message is still here; retry when ready.') from None
    return {'received': True, 'submission_id': payload['submission_id']}
