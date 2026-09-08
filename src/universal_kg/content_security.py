from __future__ import annotations

import re
from collections.abc import Iterable

from universal_kg.domain import ContentSecurity

_SIGNAL_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "instruction_override",
        re.compile(
            r"\b(?:ignore|disregard|forget|override)\b[\s\S]{0,120}"
            r"\b(?:previous|prior|above|system|developer|instructions?|rules?|policy)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "authority_impersonation",
        re.compile(
            r"(?:^|\n)\s*(?:system|developer|assistant|tool)\s*:|"
            r"</?(?:system|developer|assistant|tool)(?:\s|>)",
            re.IGNORECASE,
        ),
    ),
    (
        "secret_exfiltration",
        re.compile(
            r"\b(?:reveal|print|send|upload|exfiltrate|leak|return)\b[\s\S]{0,120}"
            r"\b(?:secret|token|password|credential|api[ -]?key|system prompt)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "tool_escalation",
        re.compile(
            r"\b(?:call|invoke|use|run|execute)\b[\s\S]{0,100}"
            r"\b(?:tool|function|command|shell|terminal)\b",
            re.IGNORECASE,
        ),
    ),
)


def assess_retrieved_content(values: Iterable[str]) -> ContentSecurity:
    scan_text = "\n".join(values)[:128_000]
    signals = [signal for signal, pattern in _SIGNAL_PATTERNS if pattern.search(scan_text)]
    return ContentSecurity(
        origin="knowledge-retrieval",
        trust="untrusted",
        instruction_authority="none",
        handling="data-only",
        injection_detected=bool(signals),
        signals=signals,
    )
