"""Aggregate documented codex exec turn usage without counting cached tokens twice."""
import json


def codex_usage(events):
    rows = []
    failed = False
    for line in events.splitlines():
        try:
            event = json.loads(line)
        except (ValueError, TypeError):
            continue
        if not isinstance(event, dict):
            continue
        if event.get('type') in ('turn.failed', 'error'):
            failed = True
        if event.get('type') == 'turn.completed':
            rows.append(event.get('usage'))
    keys = ['input_tokens', 'cached_input_tokens', 'output_tokens']
    valid = bool(rows) and all(isinstance(r, dict) and all(
        type(r.get(k)) is int and r[k] >= 0 for k in keys) and
        r['cached_input_tokens'] <= r['input_tokens'] for r in rows)
    if not valid:
        return {"available": False, "failed_event": failed, "turns": len(rows)}
    sums = {k: sum(r[k] for r in rows) for k in keys}
    sums['total_tokens'] = sums['input_tokens'] + sums['output_tokens']
    sums['uncached_input_tokens'] = sums['input_tokens'] - sums['cached_input_tokens']
    return {"available": True, "failed_event": failed, "turns": len(rows), **sums}


def api_usage(raw, request_sent):
    if not request_sent:
        return {"available": True, "input_tokens": 0, "output_tokens": 0,
                "total_tokens": 0, "source": "no_optimizer_request"}
    if not isinstance(raw, dict) or any(type(raw.get(k)) is not int or raw[k] < 0
        for k in ('input_tokens', 'output_tokens')):
        return {"available": False, "source": "optimizer_usage_unknown"}
    return {"available": True, "input_tokens": raw['input_tokens'],
            "output_tokens": raw['output_tokens'],
            "total_tokens": raw['input_tokens'] + raw['output_tokens'],
            "source": "responses_api"}


def compare_runs(baseline, optimized):
    reasons = []
    for key in ('original_prompt_sha256', 'project_fingerprint', 'model', 'reasoning', 'sandbox', 'codex_version'):
        if not baseline.get(key) or baseline.get(key) != optimized.get(key):
            reasons.append('unmatched_' + key)
    if baseline.get('execution_prompt_sha256') != baseline.get('original_prompt_sha256'):
        reasons.append('baseline_was_not_original_prompt')
    for label, run in [('baseline', baseline), ('optimized', optimized)]:
        if run.get('variant') != label:
            reasons.append(label + '_incorrect_variant')
        if run.get('quality_outcome') != 'pass':
            reasons.append(label + '_quality_not_marked_pass')
        if run.get('returncode') != 0 or run.get('execution_status') != 'completed':
            reasons.append(label + '_execution_failed')
        if not all(run.get(k, {}).get('available') for k in ('execution_usage', 'optimizer_usage')):
            reasons.append(label + '_usage_unavailable')
    if reasons:
        return {"comparable": False, "reasons": reasons, "net_savings_claim": None}
    def total(r):
        return r['execution_usage']['total_tokens'] + r['optimizer_usage']['total_tokens']
    b, o = total(baseline), total(optimized)
    return {"comparable": True, "baseline_total_tokens": b,
            "optimized_total_tokens_including_rewrite": o, "net_tokens_saved": b-o,
            "net_token_change_percent": round((b-o)/b*100, 2) if b else None,
            "baseline_seconds": baseline['elapsed_seconds'] + baseline.get('optimizer_seconds', 0),
            "optimized_seconds_including_rewrite": optimized['elapsed_seconds'] + optimized.get('optimizer_seconds', 0),
            "note": "One paired observation, not a causal savings guarantee. Cached input is a subset of input; reasoning is not added again. Token totals are not dollar costs or subscription quota. Use repeated isolated trials."}
