"""Segmented persistent conversation history management for Nova."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, List, Mapping, MutableSequence, Sequence

try:  # Python 3.9+
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
except ImportError:  # pragma: no cover - fallback for older interpreters
    from backports.zoneinfo import ZoneInfo, ZoneInfoNotFoundError  # type: ignore

MAX_MESSAGES = 40  # keep the last 20 exchanges (user+assistant)

LOGS_ROOT = Path(__file__).resolve().parent / "logs"
CONTEXT_ROOT = LOGS_ROOT / "context"
DEFAULT_CONTEXT_PATH = LOGS_ROOT / "conversation.json"
DEFAULT_SEGMENT = "general"
LEGACY_CONTEXT_PATH = Path(__file__).resolve().parent / "context.json"
try:
    LOCAL_ZONE = ZoneInfo("Europe/Berlin")
except ZoneInfoNotFoundError:  # pragma: no cover - tzdata missing
    LOCAL_ZONE = timezone(timedelta(hours=2), name="UTC+02")


def _utc_iso() -> str:
    return datetime.now(LOCAL_ZONE).replace(microsecond=0).isoformat()


def _ensure_log_dirs() -> None:
    try:
        LOGS_ROOT.mkdir(parents=True, exist_ok=True)
        CONTEXT_ROOT.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass


def _sanitize(messages: Iterable[dict]) -> List[dict]:
    cleaned: List[dict] = []
    for entry in messages:
        if not isinstance(entry, dict):
            continue
        role = entry.get("role")
        content = entry.get("content")
        if role not in {"user", "assistant"}:
            continue
        if not isinstance(content, str) or not content.strip():
            continue
        cleaned.append({"role": role, "content": content.strip()})
    return cleaned


_SEGMENT_SLUG_RE = re.compile(r"[^a-z0-9_-]+")


def _segment_slug(segment: str) -> str:
    base = segment.strip().lower()
    base = base.replace(" ", "-")
    base = _SEGMENT_SLUG_RE.sub("-", base)
    base = base.strip("-_")
    return base or "default"


def _prepare_metadata(metadata: Mapping[str, Any] | None) -> dict[str, Any]:
    if not metadata:
        return {}
    prepared: dict[str, Any] = {}
    for key, value in metadata.items():
        if not isinstance(key, str):
            key = str(key)
        try:
            json.dumps(value)
            prepared[key] = value
        except TypeError:
            prepared[key] = str(value)
    return prepared


def _extract_facts(summary: str) -> List[str]:
    facts: List[str] = []
    seen: set[str] = set()
    for line in summary.splitlines():
        text = line.strip()
        if not text:
            continue
        text = text.lstrip("-*0123456789. \t").strip()
        if not text:
            continue
        key = text.lower()
        if key in seen:
            continue
        seen.add(key)
        facts.append(text)
    return facts


class SegmentStore:
    """Write conversation snippets into per-segment files for later retrieval."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def _segment_dir(self, segment: str) -> tuple[str, Path]:
        slug = _segment_slug(segment)
        return slug, self.root / slug

    def _ensure_manifest(self, seg_dir: Path, segment: str, slug: str) -> None:
        manifest = seg_dir / "segment.json"
        if manifest.exists():
            return
        payload = {
            "segment": segment,
            "slug": slug,
            "created": _utc_iso(),
        }
        try:
            manifest.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        except OSError:
            pass

    def record_raw(self, segment: str, role: str, content: str, metadata: Mapping[str, Any] | None = None) -> None:
        slug, seg_dir = self._segment_dir(segment)
        raw_dir = seg_dir / "raw"
        try:
            raw_dir.mkdir(parents=True, exist_ok=True)
        except OSError:
            return

        timestamp = datetime.now(LOCAL_ZONE)
        ts_iso = timestamp.replace(microsecond=0).isoformat()
        ts_compact = timestamp.strftime("%Y%m%dT%H%M%S")
        digest = hashlib.sha1(f"{role}:{ts_iso}:{content}".encode("utf-8", "ignore")).hexdigest()[:8]
        filename = f"{ts_compact}_{role}_{digest}.json"
        payload: dict[str, Any] = {
            "role": role,
            "content": content,
            "text": content,
            "ts": ts_iso,
            "segment": segment,
            "slug": slug,
        }
        meta_payload = _prepare_metadata(metadata)
        if meta_payload:
            payload["metadata"] = meta_payload
        try:
            (raw_dir / filename).write_text(
                json.dumps(payload, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        except OSError:
            return
        self._ensure_manifest(seg_dir, segment, slug)

    def update_summary(
        self,
        segment: str,
        summary: str,
        facts: Sequence[str],
        *,
        updated: str | None = None,
    ) -> None:
        if not summary.strip():
            return
        slug, seg_dir = self._segment_dir(segment)
        try:
            seg_dir.mkdir(parents=True, exist_ok=True)
        except OSError:
            return

        summary_path = seg_dir / "summary.json"
        payload = {
            "segment": segment,
            "slug": slug,
            "summary": summary.strip(),
            "facts": list(facts),
            "updated": updated or _utc_iso(),
        }
        try:
            summary_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        except OSError:
            pass
        self._ensure_manifest(seg_dir, segment, slug)


_ensure_log_dirs()
_SEGMENT_STORE = SegmentStore(CONTEXT_ROOT)


def load_context(path: Path = DEFAULT_CONTEXT_PATH) -> List[dict]:
    candidate_paths = []
    if path.exists():
        candidate_paths.append(path)
    elif LEGACY_CONTEXT_PATH.exists():
        candidate_paths.append(LEGACY_CONTEXT_PATH)
    else:
        return []

    for candidate in candidate_paths:
        try:
            data = json.loads(candidate.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(data, list):
            cleaned = _sanitize(data)[-MAX_MESSAGES:]
            if candidate is LEGACY_CONTEXT_PATH and cleaned:
                save_context(cleaned, path=path)
            return cleaned
    return []


def save_context(messages: Sequence[dict], path: Path = DEFAULT_CONTEXT_PATH) -> None:
    cleaned = _sanitize(messages)[-MAX_MESSAGES:]
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        return
    try:
        path.write_text(json.dumps(cleaned, indent=2, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass


def append_message(
    history: MutableSequence[dict],
    role: str,
    content: str,
    *,
    path: Path = DEFAULT_CONTEXT_PATH,
    auto_save: bool = True,
    segment: str | None = None,
    metadata: Mapping[str, Any] | None = None,
) -> None:
    entry = {"role": role, "content": content.strip()}
    history.append(entry)
    _SEGMENT_STORE.record_raw(segment or DEFAULT_SEGMENT, role, entry["content"], metadata=metadata)
    if auto_save:
        save_context(history, path=path)


def update_segment_summary(
    segment: str,
    summary: str,
    *,
    facts: Sequence[str] | None = None,
    updated: str | None = None,
) -> None:
    parsed_facts = list(facts) if facts is not None else _extract_facts(summary)
    _SEGMENT_STORE.update_summary(segment, summary, parsed_facts, updated=updated)
