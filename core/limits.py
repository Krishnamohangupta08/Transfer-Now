"""Transfer limits shared by the UI, the packager and the upload engine (Qt-free).

``MAX_FILES_PER_TRANSFER`` is the single configurable constant for "how many
upload items may one transfer hold".  It counts *items* (files, or one ZIP
archive), never bytes — the byte cap stays in ``core.engine.MAX_BYTES``.

The limit is enforced in three places that all use this module:

* ``ui.pages.upload.UploadPage``  — admission of every pick / drop / folder scan,
* ``core.engine.TransferEngine``  — the backend check, so a direct worker or API
  call cannot bypass the UI,
* ``core.ziputil``               — the packager returns exactly one item.

"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Iterable

#: Maximum upload items (files or archives) in a single transfer.
MAX_FILES_PER_TRANSFER = 20

#: Default name of the archive built when a selection exceeds the limit.
ARCHIVE_NAME = "TransferNow_Archive.zip"

def normalized_key(path: str) -> str:
    """Identity used to detect duplicate queue entries (case-folded on Windows)."""
    try:
        real = os.path.realpath(path)
    except (OSError, ValueError):
        real = os.path.abspath(path)
    return os.path.normcase(real)


@dataclass
class AdmissionPlan:
    """Result of :func:`plan_admission` — pure data, no side effects."""
    accepted: list[str] = field(default_factory=list)     # fit in the free slots
    overflow: list[str] = field(default_factory=list)     # would exceed the limit
    duplicates: list[str] = field(default_factory=list)   # already queued / repeated in the batch
    free_slots: int = 0

    @property
    def needs_zip(self) -> bool:
        return bool(self.overflow)


def plan_admission(existing: Iterable[str], new: Iterable[str],
                   limit: int = MAX_FILES_PER_TRANSFER) -> AdmissionPlan:
    """Decide which of ``new`` fit next to ``existing`` without exceeding ``limit``.

    Duplicates (same real path, also repeated inside ``new``) are reported and
    never counted.  Order is preserved.  The caller must hold whatever lock
    protects ``existing`` while it applies the plan, which is what makes
    concurrent selections unable to exceed the limit.
    """
    existing = list(existing)
    seen = {normalized_key(p) for p in existing}
    plan = AdmissionPlan(free_slots=max(0, limit - len(existing)))
    free = plan.free_slots
    for p in new:
        key = normalized_key(p)
        if key in seen:
            plan.duplicates.append(p)
            continue
        seen.add(key)
        if free > 0:
            plan.accepted.append(p)
            free -= 1
        else:
            plan.overflow.append(p)
    return plan


def check_item_count(files: Iterable[str], limit: int | None = None) -> None:
    """Backend guard used by the engine: raise ``ValueError`` on duplicates or
    more than the limit."""
    limit = MAX_FILES_PER_TRANSFER if limit is None else limit
    files = list(files)
    keys = {normalized_key(p) for p in files}
    if len(keys) != len(files):
        raise ValueError("The same file was added more than once.")
    if len(files) > limit:
        raise ValueError(f"A transfer can hold at most {limit} items ({len(files)} given). "
                         "Combine the extra files into a ZIP archive or start a separate transfer.")
