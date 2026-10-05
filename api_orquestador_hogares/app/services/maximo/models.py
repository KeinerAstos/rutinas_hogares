from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class RelatedRecord:
    key: str
    description: str = ""
    record_class: str = ""
    created_at: str = ""
    status: str = ""
    relationship: str = ""


@dataclass
class ValidationResult:
    ok: bool
    ot: str
    classification: str = ""
    incidents: list[RelatedRecord] = field(default_factory=list)

    attachments: list[dict] = field(default_factory=list)
    notes_checked: int = 0
    notes_with_attachments: int = 0

    response_text: str = ""
    code: str = ""
    error: str = ""