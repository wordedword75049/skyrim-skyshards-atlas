"""Poll a save directory, publishing only stable, successfully parsed snapshots."""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import NamedTuple

from .ess import SaveAccessError, SaveFormatError, parse_save
from .models import Catalog, ProgressSnapshot
from .progress import classify, empty_snapshot


LOG = logging.getLogger(__name__)


class FileIdentity(NamedTuple):
    path: Path
    size: int
    mtime_ns: int
    catalog_version: str


class SaveWatcher:
    def __init__(self, directory: Path, catalog: Catalog, *, stability_delay: float = 1.0):
        self.directory = directory
        self.catalog = catalog
        self.stability_delay = stability_delay
        self._lock = threading.Lock()
        self._snapshot = empty_snapshot(catalog)
        self._last_attempt: FileIdentity | None = None
        self._last_success: FileIdentity | None = None
        self._parse_attempts = 0
        self.running = False

    @property
    def snapshot(self) -> ProgressSnapshot:
        with self._lock:
            return self._snapshot

    @property
    def parse_attempts(self) -> int:
        with self._lock:
            return self._parse_attempts

    def _sample_identity(self, path: Path) -> FileIdentity:
        stat = path.stat()
        if not path.is_file():
            raise OSError("candidate is not a regular file")
        return FileIdentity(path.resolve(strict=True), stat.st_size, stat.st_mtime_ns,
                            self.catalog.version)

    def _candidates(self) -> list[FileIdentity]:
        candidates = []
        for entry in self.directory.iterdir():
            if entry.suffix.lower() == ".ess":
                try:
                    candidates.append(self._sample_identity(entry))
                except OSError:
                    continue
        candidates.sort(key=lambda item: (item.mtime_ns, item.path.name), reverse=True)
        return candidates

    def _co_save_status(self, path: Path) -> str:
        co_save = path.with_suffix(".skse")
        if not co_save.exists():
            return "missing"
        try:
            first = co_save.stat()
            time.sleep(self.stability_delay)
            second = co_save.stat()
            if (first.st_size, first.st_mtime_ns) != (second.st_size, second.st_mtime_ns):
                return "pending"
            return "stable"
        except OSError:
            return "unreadable"

    def _show_error(self, candidate: FileIdentity | None, message: str) -> ProgressSnapshot:
        if self._snapshot.newer_save_error == message and self._snapshot.newest_candidate == (
                candidate.path.name if candidate else None):
            return self._snapshot
        LOG.warning("Save refresh failed: %s", message)
        self._snapshot = self._snapshot.model_copy(update={
            "is_newest": False,
            "newest_candidate": candidate.path.name if candidate else None,
            "newer_save_error": message,
        })
        return self._snapshot

    def refresh(self, *, force: bool = False) -> ProgressSnapshot:
        """Scan once. Serialization through this lock prevents overlapping parses."""
        with self._lock:
            try:
                candidates = self._candidates()
            except OSError as exc:
                return self._show_error(None, f"Save directory unavailable: {exc}")
            if not candidates:
                return self._show_error(None, f"No .ess saves found in directory {self.directory}")
            candidate = candidates[0]
            if not force and candidate == self._last_attempt:
                if candidate == self._last_success and not self._snapshot.is_newest:
                    self._snapshot = self._snapshot.model_copy(update={
                        "is_newest": True, "newest_candidate": candidate.path.name,
                        "newer_save_error": None,
                    })
                return self._snapshot
            try:
                time.sleep(self.stability_delay)
                if self._sample_identity(candidate.path) != candidate:
                    return self._show_error(candidate, "Newest ESS is not stable yet; waiting for size and timestamp to settle")
                self._parse_attempts += 1
                LOG.info("Parsing save %s", candidate.path.name)
                parsed = parse_save(candidate.path, self.catalog)
                if self._sample_identity(candidate.path) != candidate:
                    return self._show_error(candidate, "Newest ESS changed during parsing; result discarded")
                co_save_status = self._co_save_status(candidate.path)
                if self._sample_identity(candidate.path) != candidate:
                    return self._show_error(candidate, "Newest ESS changed before publication; result discarded")
                modified = datetime.fromtimestamp(candidate.mtime_ns / 1_000_000_000, timezone.utc)
                snapshot = classify(parsed, self.catalog, modified_at=modified, co_save_status=co_save_status)
                if co_save_status in ("pending", "unreadable"):
                    snapshot = snapshot.model_copy(update={
                        "warnings": snapshot.warnings + (f"Matching SKSE co-save: {co_save_status}",)})
                self._snapshot = snapshot
                self._last_attempt = candidate
                self._last_success = candidate
                LOG.info("Parsed %s: %d collected, %d uncollected, %d unknown",
                         candidate.path.name, snapshot.collected_count, snapshot.uncollected_count,
                         snapshot.unknown_count)
                for warning in snapshot.warnings:
                    LOG.warning("Save validation: %s", warning)
                return snapshot
            except SaveAccessError as exc:
                return self._show_error(candidate, f"Cannot read newest save {candidate.path.name}: {exc}")
            except OSError as exc:
                return self._show_error(candidate, f"Cannot access newest save {candidate.path.name}: {exc}")
            except (SaveFormatError, ValueError) as exc:
                self._last_attempt = candidate
                return self._show_error(candidate, f"Cannot parse newest save {candidate.path.name}: {exc}")
