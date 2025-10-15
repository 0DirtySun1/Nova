"""Simple persistent conversation history management for Nova."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable, List, MutableSequence, Sequence

DEFAULT_CONTEXT_PATH = Path(__file__).resolve().parent / "context.json"
MAX_MESSAGES = 40  # keep the last 20 exchanges (user+assistant)


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


def load_context(path: Path = DEFAULT_CONTEXT_PATH) -> List[dict]:
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if isinstance(data, list):
        return _sanitize(data)[-MAX_MESSAGES:]
    return []


def save_context(messages: Sequence[dict], path: Path = DEFAULT_CONTEXT_PATH) -> None:
    cleaned = _sanitize(messages)[-MAX_MESSAGES:]
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
) -> None:
    entry = {"role": role, "content": content.strip()}
    history.append(entry)
    if auto_save:
        save_context(history, path=path)
