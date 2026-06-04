"""Domain models for audience templates."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class AudienceTemplate:
    template_id: str
    name: str
    category: str
    description: str
    rule: dict[str, Any]
    recommended_channels: tuple[str, ...]
    recommended_trigger: str
    tags: tuple[str, ...]
