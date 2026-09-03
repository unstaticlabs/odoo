"""Deterministic timestamp aggregation for migrated document identities."""

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class SourceTimestamps:
    created_at: datetime
    modified_at: datetime


@dataclass(frozen=True)
class SourceAttachment:
    source_id: int
    name: str
    res_model: str
    file_size: int | None
    mimetype: str | None
    record_key: tuple | None
    timestamps: SourceTimestamps


def merge_timestamps(
    current: SourceTimestamps | None,
    created_at: datetime,
    modified_at: datetime | None,
) -> SourceTimestamps:
    incoming = SourceTimestamps(created_at, modified_at or created_at)
    if not current:
        return incoming
    return SourceTimestamps(
        min(current.created_at, incoming.created_at),
        max(current.modified_at, incoming.modified_at),
    )


def add_timestamps(mapping, key, created_at, modified_at=None):
    if key and created_at:
        mapping[key] = merge_timestamps(
            mapping.get(key),
            created_at,
            modified_at,
        )
    return mapping.get(key)


def select_source_attachment(
    candidates: list[SourceAttachment],
    *,
    name: str,
    res_model: str,
    file_size: int | None,
    mimetype: str | None,
    record_key: tuple | None,
    occurrence: int = 0,
) -> SourceAttachment | None:
    """Resolve one source attachment without guessing across business records."""
    narrowed = candidates
    for attribute, value in (
        ("name", name),
        ("res_model", res_model),
        ("file_size", file_size),
        ("mimetype", mimetype),
    ):
        matches = [item for item in narrowed if getattr(item, attribute) == value]
        if not matches:
            return None
        narrowed = matches
    if record_key is not None:
        matches = [item for item in narrowed if item.record_key == record_key]
        if not matches:
            return None
        narrowed = matches
    if len(narrowed) == 1:
        return narrowed[0]
    if not narrowed or record_key is None:
        return None
    ordered = sorted(narrowed, key=lambda item: item.source_id)
    if occurrence >= len(ordered):
        return None
    return ordered[occurrence]
