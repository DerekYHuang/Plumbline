"""What a check returns: findings, each with evidence and a repair hint.

One `CheckResult` per query. It holds zero or more `Finding`s:

    Finding(
        rule="VALUE_CASE",                 # which check fired (stable code, safe to match on)
        severity="error",                  # error = the answer is very likely wrong
                                           # warning = suspicious, worth a look
        message="'Locally Funded' is not a value of schools.FundingType ...",
        evidence={...},                    # the facts from the catalog that prove it
        hint="Use 'Locally funded'.",      # what to change; written for a human or an AI
        snippet="s.FundingType = 'Locally Funded'",   # the part of the query concerned
    )

A query *passes* when it has no error-level findings. Warnings never fail a query on their
own: they mark things the catalog can't prove either way.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

ERROR = "error"
WARNING = "warning"


@dataclass
class Finding:
    rule: str
    severity: str
    message: str
    hint: str = ""
    snippet: str = ""
    evidence: dict[str, Any] = field(default_factory=dict)


@dataclass
class CheckResult:
    sql: str
    findings: list[Finding] = field(default_factory=list)
    # Things the user should know about the check itself (a rule was skipped, the
    # catalog is out of date, ...). Not findings about the query.
    notes: list[str] = field(default_factory=list)
    # Set when the SQL could not be parsed at all; no rule ran.
    parse_error: str | None = None

    @property
    def passed(self) -> bool:
        return self.parse_error is None and not any(f.severity == ERROR for f in self.findings)

    @property
    def errors(self) -> list[Finding]:
        return [f for f in self.findings if f.severity == ERROR]

    @property
    def warnings(self) -> list[Finding]:
        return [f for f in self.findings if f.severity == WARNING]

    def to_dict(self) -> dict[str, Any]:
        return {"passed": self.passed, **asdict(self)}
