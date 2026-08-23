"""Streaming, fail-closed loader for the HARTH protocol-v2 archive.

This module deliberately stops at deterministic, windowed records.  It never computes
scientific metrics and never materializes the archive or a CSV in memory.
"""

from __future__ import annotations

import csv
import hashlib
import io
import math
import posixpath
import re
import zipfile
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .engine import ProtocolFailure, Window

SENSOR_COLUMNS = ("back_x", "back_y", "back_z", "thigh_x", "thigh_y", "thigh_z")
DEFAULT_WINDOW_SIZE = 128
DEFAULT_STRIDE = 64
_SUBJECT_RE = re.compile(r"^(?:s|subject)[-_ ]?([0-9]+)$", re.IGNORECASE)


class LoaderFailure(ProtocolFailure):
    """An archive, identity, ordering, or eligibility violation."""


@dataclass(frozen=True)
class RawRow:
    subject: str
    session: str
    timestamp: datetime
    label: str
    sensors: tuple[float, ...]
    file: str
    row_number: int


@dataclass(frozen=True)
class LoadedArchive:
    windows: tuple[Window, ...]
    manifest: dict[str, Any]
    rows: int


def _normal_path(name: str) -> str:
    if "\\" in name or not name or name.startswith("/") or name.startswith("\\"):
        raise LoaderFailure(f"unsafe archive member path: {name!r}")
    normalized = posixpath.normpath(name)
    if normalized in (".", "") or normalized == ".." or normalized.startswith("../"):
        raise LoaderFailure(f"unsafe archive member path: {name!r}")
    return normalized


def _parse_time(value: str) -> datetime:
    text = value.strip()
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        try:
            parsed = datetime.fromtimestamp(float(text), UTC)
        except (ValueError, OverflowError, OSError) as exc:
            raise LoaderFailure(f"invalid timestamp: {value!r}") from exc
    if parsed.tzinfo is None:
        raise LoaderFailure("timestamp must include timezone")
    return parsed.astimezone(UTC)


def _identity(value: Mapping[str, str], keys: tuple[str, ...], kind: str) -> str:
    present = [value[k].strip() for k in keys if k in value and value[k].strip()]
    if not present:
        raise LoaderFailure(f"missing {kind} identity")
    if len(set(present)) != 1:
        raise LoaderFailure(f"ambiguous {kind} identity")
    return present[0]


def _subject(value: Mapping[str, str], member: str) -> str:
    if any(k in value and value[k].strip() for k in ("subject", "subject_id", "participant")):
        explicit = _identity(value, ("subject", "subject_id", "participant"), "subject")
        stem = Path(member).stem.split("_")[0]
        match = _SUBJECT_RE.match(stem)
        if match and explicit != f"S{int(match.group(1)):03d}":
            raise LoaderFailure("ambiguous subject identity")
        return explicit
    stem = Path(member).stem.split("_")[0]
    match = _SUBJECT_RE.match(stem)
    if not match:
        raise LoaderFailure(f"ambiguous subject identity in {member!r}")
    return f"S{int(match.group(1)):03d}"


def _session(value: Mapping[str, str], member: str) -> str:
    if any(k in value and value[k].strip() for k in ("session", "session_id", "recording")):
        return _identity(value, ("session", "session_id", "recording"), "session")
    stem = Path(member).stem
    parts = stem.split("_")
    if len(parts) < 2 or not parts[1]:
        raise LoaderFailure(f"ambiguous session identity in {member!r}")
    return parts[1]


def _iter_member_rows(stream: io.TextIOBase, member: str) -> Iterator[RawRow]:
    reader = csv.DictReader(stream)
    if reader.fieldnames is None:
        raise LoaderFailure(f"missing CSV header: {member}")
    fields = {field.strip() for field in reader.fieldnames if field}
    timestamp_key = next((k for k in ("timestamp", "time", "datetime") if k in fields), None)
    label_key = next((k for k in ("label", "activity", "class") if k in fields), None)
    if not timestamp_key or not label_key or not set(SENSOR_COLUMNS) <= fields:
        raise LoaderFailure(f"missing required columns in {member}")
    last: datetime | None = None
    for number, row in enumerate(reader, 2):
        if None in row:
            raise LoaderFailure(f"extra CSV fields at {member}:{number}")
        subject, session = _subject(row, member), _session(row, member)
        timestamp = _parse_time(row[timestamp_key])
        if last is not None and timestamp <= last:
            raise LoaderFailure(f"duplicate/nonmonotonic timestamp at {member}:{number}")
        last = timestamp
        try:
            values = tuple(float(row[key]) for key in SENSOR_COLUMNS)
        except (KeyError, TypeError, ValueError) as exc:
            raise LoaderFailure(f"invalid sensor value at {member}:{number}") from exc
        if not all(math.isfinite(item) for item in values):
            raise LoaderFailure(f"non-finite sensor value at {member}:{number}")
        label = row[label_key].strip()
        if not label:
            raise LoaderFailure(f"empty label at {member}:{number}")
        yield RawRow(subject, session, timestamp, label, values, member, number)


def _window_features(rows: Sequence[RawRow]) -> tuple[float, ...]:
    # The engine's frozen representation is per-channel mean and standard deviation.
    columns = list(zip(*(row.sensors for row in rows), strict=False))
    means = [sum(column) / len(column) for column in columns]
    stds = [
        math.sqrt(sum((x - mean) ** 2 for x in column) / len(column))
        for column, mean in zip(columns, means, strict=False)
    ]
    return tuple(means + stds)


def load_harth_archive(
    archive: str | Path,
    classes: Sequence[str],
    *,
    window_size: int = DEFAULT_WINDOW_SIZE,
    stride: int = DEFAULT_STRIDE,
    protocol_hash: str | None = None,
    code_hash: str | None = None,
) -> LoadedArchive:
    """Stream a ZIP archive and return every complete, session-safe canonical window."""
    if window_size <= 0 or stride <= 0:
        raise LoaderFailure("window size and stride must be positive")
    vocabulary = tuple(str(item) for item in classes)
    if not vocabulary or len(set(vocabulary)) != len(vocabulary):
        raise LoaderFailure("class vocabulary must be frozen, non-empty, and unique")
    path = Path(archive)
    if not path.is_file():
        raise LoaderFailure(f"archive is missing: {path}")
    files: list[dict[str, Any]] = []
    windows: list[Window] = []
    total_rows = 0
    try:
        with zipfile.ZipFile(path) as bundle:
            members: dict[str, zipfile.ZipInfo] = {}
            for info in bundle.infolist():
                name = _normal_path(info.filename)
                if name in members:
                    raise LoaderFailure(f"duplicate normalized archive member: {name}")
                if info.is_dir() or name.lower().endswith("/"):
                    continue
                if info.external_attr >> 16 & 0o170000 == 0o120000:
                    raise LoaderFailure(f"symlink archive member: {name}")
                members[name] = info
            for name in sorted(members):
                if not name.lower().endswith((".csv", ".csv.gz")):
                    continue
                info = members[name]
                file_hash = hashlib.sha256()
                rows: list[RawRow] = []
                with bundle.open(info) as binary:
                    raw = binary.read(1024 * 1024)
                    while raw:
                        file_hash.update(raw)
                        raw = binary.read(1024 * 1024)
                    binary.seek(0)
                    with io.TextIOWrapper(binary, encoding="utf-8", newline="") as text:
                        rows = list(_iter_member_rows(text, name))
                total_rows += len(rows)
                for start in range(0, max(0, len(rows) - window_size + 1), stride):
                    chunk = rows[start : start + window_size]
                    if len(chunk) < window_size:
                        continue
                    if len({(row.subject, row.session) for row in chunk}) != 1:
                        raise LoaderFailure(f"window crosses subject/session boundary in {name}")
                    labels = {row.label for row in chunk}
                    if len(labels) != 1 or next(iter(labels)) not in vocabulary:
                        raise LoaderFailure(f"label outside frozen vocabulary in {name}")
                    identity = f"{name}:{chunk[0].row_number}-{chunk[-1].row_number}"
                    windows.append(
                        Window(
                            chunk[0].subject,
                            chunk[0].session,
                            next(iter(labels)),
                            _window_features(chunk),
                            identity,
                        )
                    )
                files.append(
                    {
                        "path": name,
                        "bytes": info.file_size,
                        "sha256": file_hash.hexdigest(),
                        "rows": len(rows),
                    }
                )
    except zipfile.BadZipFile as exc:
        raise LoaderFailure("invalid ZIP archive") from exc
    windows.sort(key=lambda item: (item.subject, item.session, item.provenance))
    subjects = sorted({item.subject for item in windows})
    if len(subjects) < 2:
        raise LoaderFailure("fewer than two eligible subjects")
    if len(subjects) > 22:
        raise LoaderFailure("maximum 22 outer folds exceeded")
    manifest = {
        "archive_sha256": _file_hash(path),
        "files": files,
        "rows": total_rows,
        "windows": len(windows),
        "subjects": subjects,
        "class_vocabulary": list(vocabulary),
        "window_size": window_size,
        "stride": stride,
        "protocol_hash": protocol_hash,
        "code_hash": code_hash,
        "scientific_metrics": False,
    }
    return LoadedArchive(tuple(windows), manifest, total_rows)


def _file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


# Explicit aliases make the boundary easy to discover for callers.
stream_harth_archive = load_harth_archive
load_archive = load_harth_archive
