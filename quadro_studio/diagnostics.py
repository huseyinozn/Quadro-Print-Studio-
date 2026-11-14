"""Structured diagnostics shared by every stage.

Every problem is reported with a stable code, a severity, a human message
and (where possible) an actionable hint. ``error`` diagnostics always block
generation; ``warning`` diagnostics are surfaced in the report and in the
G-code header but do not block; ``info`` is informational.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict


@dataclass
class Diagnostic:
    code: str
    severity: str          # "error" | "warning" | "info"
    message: str
    hint: str = ""
    where: dict = field(default_factory=dict)

    def as_dict(self):
        d = asdict(self)
        if not d["hint"]:
            d.pop("hint")
        if not d["where"]:
            d.pop("where")
        return d


class Diagnostics:
    def __init__(self):
        self.items: list[Diagnostic] = []

    def add(self, code, severity, message, hint="", **where):
        self.items.append(Diagnostic(code, severity, message, hint, where))

    def error(self, code, message, hint="", **where):
        self.add(code, "error", message, hint, **where)

    def warning(self, code, message, hint="", **where):
        self.add(code, "warning", message, hint, **where)

    def info(self, code, message, hint="", **where):
        self.add(code, "info", message, hint, **where)

    def extend(self, other: "Diagnostics"):
        self.items.extend(other.items)

    @property
    def errors(self):
        return [d for d in self.items if d.severity == "error"]

    @property
    def warnings(self):
        return [d for d in self.items if d.severity == "warning"]

    def ok(self):
        return not self.errors

    def as_list(self):
        return [d.as_dict() for d in self.items]

    def render(self):
        out = []
        for d in self.items:
            loc = (" [" + ", ".join("%s=%s" % kv for kv in d.where.items()) + "]") if d.where else ""
            out.append("%-7s %s: %s%s" % (d.severity.upper(), d.code, d.message, loc))
            if d.hint:
                out.append("        -> " + d.hint)
        return "\n".join(out)


class StageError(Exception):
    """Raised when a stage cannot continue; carries the diagnostics."""

    def __init__(self, diags: Diagnostics):
        super().__init__(diags.render())
        self.diags = diags
