# SPDX-License-Identifier: GPL-3.0-or-later
"""Pure prompt checks. Estimates are deliberately separate from measured usage."""
import hashlib
import math
import re

MAX_PROMPT_BYTES = 128_000
GUARD = re.compile(r"\b(must|never|do not|don't|only|without|preserve|keep|require|required|at least|at most|exactly|acceptance|before|after|unless)\b", re.I)
LITERALS = re.compile(r"```[\s\S]*?```|~~~[\s\S]*?~~~|`[^`\n]+`|https?://[^\s<>]+|(?<!\w)(?:[./~]|[A-Za-z]:[\\/])[^\s,;]+|\b[\w.-]+\.(?:py|js|ts|tsx|jsx|json|yaml|yml|toml|md|go|rs|sql|sh|txt)\b|(?<![\w.])[+-]?\d+(?:\.\d+)?%?(?![\w.])")
SECRET = re.compile(r"\b(?:sk-[A-Za-z0-9_-]{12,}|rpa_[A-Za-z0-9_-]{12,}|gh[pousr]_[A-Za-z0-9]{16,}|AKIA[A-Z0-9]{16})\b|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----|(?i:(?:api[_-]?key|password|secret|access[_-]?token)\s*[:=]\s*['\"]?[^\s'\"]{8,})")


def digest(text):
    return hashlib.sha256(text.encode()).hexdigest()


def metrics(text):
    return {"characters": len(text), "utf8_bytes": len(text.encode()),
            "lines": len(text.splitlines()), "estimated_tokens": math.ceil(len(text) / 4),
            "token_count_kind": "rough_characters_divided_by_four_not_model_tokenization"}


def protected_spans(text):
    # This is a conservative tripwire, not a semantic equivalence proof.
    spans = LITERALS.findall(text)
    for line in text.splitlines():
        if GUARD.search(line):
            spans.append(line.strip())
    return list(dict.fromkeys(s for s in spans if s))


def check_candidate(original, candidate, must_keep=()):
    errors = []
    if not isinstance(candidate, str) or not candidate.strip():
        return ["empty_or_nontext_candidate"]
    if len(candidate) >= len(original):
        errors.append("candidate_not_shorter")
    for span in [*protected_spans(original), *must_keep]:
        if span not in candidate:
            # Avoid echoing potentially sensitive omitted literals into logs.
            errors.append("protected_span_missing:" + digest(span)[:12])
    return errors


def audit_prompt(text):
    paragraphs = [x.strip() for x in re.split(r"\n\s*\n", text) if x.strip()]
    return {**metrics(text), "duplicate_paragraphs": len(paragraphs) - len(set(paragraphs)),
            "protected_span_count": len(protected_spans(text)),
            "possible_secret": bool(SECRET.search(text)),
            "model_rewrite_worth_reviewing": len(text) >= 1600}


def tidy(text):
    # No rewriting of code, constraints, paths, order, or prose in local mode.
    return text.strip('\r\n')
