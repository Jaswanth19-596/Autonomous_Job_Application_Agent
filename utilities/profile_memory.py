"""Persistent, scoped answers supplied directly by the user."""

import fcntl
import hashlib
import json
import os
from pathlib import Path
import tempfile
from datetime import datetime, timezone

PROFILE_PATH = Path(__file__).resolve().parents[1] / 'user/user_profile.json'


def load_profile(path=PROFILE_PATH):
    with Path(path).open(encoding='utf-8') as stream:
        return json.load(stream)


def answer_key(question, scope, context):
    normalized = ' '.join(question.casefold().split()).rstrip('?. ')
    return hashlib.sha256(json.dumps([normalized, scope, context]).encode()).hexdigest()


def question_context(state, scope):
    details = state.get('job_details') or {}
    if scope == 'global':
        return ''
    if scope == 'employer':
        employer = details.get('companyName') or details.get('company')
        if isinstance(employer, str) and employer.strip():
            return employer.strip().casefold()
    return state.get('job_url') or state.get('actual_job_url', '')


def save_answer(question, answer, scope='job', context='', path=PROFILE_PATH):
    """Atomically merge one exact reply; retries replace the same question key."""
    if not isinstance(answer, str) or not answer.strip() or not question.strip():
        raise ValueError('A question and nonempty text answer are required')
    if scope not in {'global', 'employer', 'job'} or (scope != 'global' and not context):
        raise ValueError('A scoped answer needs an explicit context')
    path = Path(path)
    with path.with_suffix('.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        profile = load_profile(path)
        answers = profile.setdefault('application_answers', {})
        answers[answer_key(question, scope, context)] = {
            'question': question, 'answer': answer.strip(), 'scope': scope,
            'context': context, 'source': 'user',
            'updated_at': datetime.now(timezone.utc).isoformat(),
        }
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent,
                                             delete=False) as stream:
                temporary = stream.name
                json.dump(profile, stream, ensure_ascii=False, indent=2)
                stream.write('\n')
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            if temporary and os.path.exists(temporary):
                os.unlink(temporary)
        return profile


def recalled_answer(profile, question, scope, context):
    answers = profile.get('application_answers', {})
    item = answers.get(answer_key(question, scope, context))
    if item:
        return item.get('answer')
    # Also honor global and older unscoped records regardless of their stored key.
    normalized = ' '.join(question.casefold().split()).rstrip('?. ')
    for item in reversed(list(answers.values())):
        same_question = ' '.join(item.get('question', '').casefold().split()).rstrip('?. ') == normalized
        applicable = item.get('scope', 'global') == 'global' or (
            item.get('scope') == scope and item.get('context') == context
        )
        if same_question and applicable:
            return item.get('answer')
    return None


def applicable_profile(profile, state):
    """Do not expose another employer's/job's answers as applicable facts."""
    result = dict(profile)
    result['application_answers'] = {
        key: item for key, item in profile.get('application_answers', {}).items()
        if item.get('scope', 'global') == 'global' or
        item.get('context') == question_context(state, item.get('scope', 'job'))
    }
    return result
