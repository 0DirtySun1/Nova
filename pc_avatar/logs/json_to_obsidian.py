"""Convert segmented context logs into a tidy Obsidian vault."""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import unicodedata
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

try:
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
except ImportError:  # pragma: no cover - for older Python
    from backports.zoneinfo import ZoneInfo, ZoneInfoNotFoundError  # type: ignore

BASE_DIR = Path(__file__).resolve().parent
ROOT = BASE_DIR / "context"
VAULT = BASE_DIR / "obsidian_vault"
try:
    LOCAL_ZONE = ZoneInfo("Europe/Berlin")
except ZoneInfoNotFoundError:  # pragma: no cover - tzdata missing
    LOCAL_ZONE = timezone(timedelta(hours=2), name="UTC+02")


def list_segments() -> List[str]:
    if not ROOT.exists():
        return []
    return sorted([p.name for p in ROOT.iterdir() if p.is_dir()])


def safe_filename(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value)
    normalized = re.sub(r"[^\w\s-]", "", normalized).strip()
    normalized = re.sub(r"[-\s]+", "-", normalized)
    cleaned = normalized[:120]
    if cleaned:
        return cleaned
    return hashlib.sha1(value.encode("utf-8", "ignore")).hexdigest()[:10]


def mkfront(title: str, tags: Sequence[str], created: str) -> str:
    tag_text = ", ".join(tags)
    lines = [
        "---",
        f"title: {title}",
        f"tags: [{tag_text}]",
        f"created: {created}",
        "---",
        "",
    ]
    return "\n".join(lines)


def read_summary(segment: str) -> Dict[str, object]:
    path = ROOT / segment / "summary.json"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def collect_raw(segment: str) -> List[Tuple[str, Dict[str, object]]]:
    raw_dir = ROOT / segment / "raw"
    if not raw_dir.exists():
        return []
    collected: List[Tuple[str, Dict[str, object]]] = []
    for raw_file in sorted(raw_dir.iterdir()):
        if not raw_file.is_file():
            continue
        try:
            payload = json.loads(raw_file.read_text(encoding="utf-8"))
        except Exception:
            continue
        collected.append((raw_file.name, payload))
    return collected


def tidy_lines(lines: Iterable[str]) -> List[str]:
    seen: set[str] = set()
    cleaned_lines: List[str] = []
    for line in lines:
        raw = str(line).replace("**", "").replace("’", "'")
        cleaned = re.sub(r"\s+", " ", raw).strip()
        if not cleaned:
            continue
        lower = cleaned.lower()
        if lower in {
            "sure thing! here’s a quick summary:",
            "sure thing! here's a quick summary:",
            "let me know if you need anything else!",
            "assistance ready: i’m here to help with coding questions or features you want to implement!",
            "assistance ready: i'm here to help with coding questions or features you want to implement!",
        }:
            continue
        if lower.endswith(":") and len(cleaned.split()) <= 6:
            continue
        if lower.startswith("i'm here to help"):
            continue
        if lower.startswith("im here to help"):
            continue
        if lower not in seen:
            seen.add(lower)
            cleaned_lines.append(cleaned)
    return cleaned_lines


def bulletize(text: str) -> List[str]:
    raw_lines = []
    for line in text.splitlines():
        cleaned = line.strip()
        if not cleaned:
            continue
        cleaned = cleaned.lstrip("-*•0123456789. \t").strip()
        if cleaned:
            raw_lines.append(cleaned)
    return tidy_lines(raw_lines)


SNIPPET_LIMIT = 68
STOP_WORDS = {
    "the",
    "and",
    "that",
    "with",
    "from",
    "this",
    "they",
    "have",
    "what",
    "whats",
    "lets",
    "lets",
    "your",
    "about",
    "into",
    "there",
    "some",
    "like",
    "just",
    "you're",
    "youre",
    "going",
    "over",
    "here",
    "want",
    "need",
    "really",
    "sure",
    "okay",
    "cant",
    "can't",
    "were",
    "we're",
    "back",
    "left",
    "right",
    "still",
    "look",
    "looks",
    "it's",
    "its",
    "song",
    "there's",
    "theres",
    "maybe",
    "going",
    "gonna",
    "really",
    "thing",
}


def first_line_snip(value: str, limit: int = SNIPPET_LIMIT) -> str:
    stripped = value.strip()
    if not stripped:
        return "Untitled note"
    first_line = stripped.splitlines()[0].strip()
    return (first_line[: limit - 2] + "..") if len(first_line) > limit else first_line


def extract_keywords(text: str, limit: int = 5) -> List[str]:
    tokens = re.findall(r"[A-Za-z0-9']{3,}", text.lower())
    keywords: List[str] = []
    seen: set[str] = set()
    for token in tokens:
        token_clean = token.replace("'", "")
        if token_clean in STOP_WORDS:
            continue
        normalized = re.sub(r"[^a-z0-9]", "", token_clean)
        if len(normalized) <= 3:
            continue
        if normalized in seen:
            continue
        seen.add(normalized)
        keywords.append(normalized)
        if len(keywords) >= limit:
            break
    return keywords


def prepare_raw_entries(
    segment: str,
    raw_items: List[Tuple[str, Dict[str, object]]],
) -> List[Dict[str, object]]:
    entries: List[Dict[str, object]] = []
    for raw_name, payload in raw_items:
        text = str(payload.get("text") or payload.get("content") or "").strip()
        iso, date_label, time_label = parse_timestamp(payload.get("ts"))
        role_value = str(payload.get("role") or "assistant").strip().lower()
        role_display = "User" if role_value == "user" else "Assistant"
        snippet = first_line_snip(text, 120)
        keywords = extract_keywords(text)
        keywords_line = ", ".join(keywords[:4])
        quick_summary = snippet
        digest = hashlib.sha1(f"{raw_name}:{iso}:{text}".encode("utf-8", "ignore")).hexdigest()[:6]
        file_stem = safe_filename(f"{date_label}-{time_label}-{role_display}-{digest}")
        note_title = f"{date_label} {time_label} {role_display}"
        alias = first_line_snip(f"{role_display}: {snippet}", 72)
        entries.append(
            {
                "raw_name": raw_name,
                "text": text,
                "iso": iso,
                "date": date_label,
                "time": time_label,
                "role": role_display,
                "snippet": snippet,
                "keywords": keywords,
                "keywords_line": keywords_line,
                "quick_summary": quick_summary,
                "note_title": note_title,
                "file_stem": file_stem,
                "link_path": f"{segment}/{file_stem}",
                "alias": alias,
                "metadata": payload.get("metadata") if isinstance(payload, dict) else None,
            }
        )
    return entries


def parse_timestamp(value: Optional[str]) -> Tuple[str, str, str]:
    if value:
        raw = str(value)
    else:
        raw = None

    if raw:
        candidate = raw.replace("Z", "+00:00")
        try:
            dt = datetime.fromisoformat(candidate)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=LOCAL_ZONE)
            else:
                dt = dt.astimezone(LOCAL_ZONE)
        except ValueError:
            dt = datetime.now(LOCAL_ZONE)
    else:
        dt = datetime.now(LOCAL_ZONE)

    dt = dt.astimezone(LOCAL_ZONE)
    iso = dt.replace(microsecond=0).isoformat()
    date_label = dt.date().isoformat()
    time_label = dt.strftime("%H:%M")
    return iso, date_label, time_label


def create_vault() -> None:
    if VAULT.exists():
        print("Removing existing vault directory (obsidian_vault).")
        shutil.rmtree(VAULT)
    VAULT.mkdir()
    (VAULT / ".obsidian").mkdir(exist_ok=True)


def build_segment_notes(segment: str) -> Optional[str]:
    summary = read_summary(segment)
    raw_items = collect_raw(segment)

    segment_dir = VAULT / segment
    segment_dir.mkdir(parents=True, exist_ok=True)
    for leftover in segment_dir.glob("*Summary*.md"):
        try:
            leftover.unlink()
        except OSError:
            pass

    entries = prepare_raw_entries(segment, raw_items)
    summary_title = write_summary_note(segment, summary, entries)
    write_raw_notes(segment, summary_title, entries)
    return summary_title


def write_summary_note(
    segment: str,
    summary: Dict[str, object],
    entries: List[Dict[str, object]],
) -> str:
    display_segment = segment.replace("-", " ").title()
    summary_title = f"{display_segment} Summary"
    created = summary.get("updated") if isinstance(summary, dict) else None
    created_str, _, _ = parse_timestamp(str(created) if created else None)

    tags = [f"segment/{segment}", "summary"]
    content = mkfront(summary_title, tags, created_str)

    summary_text = "" if not isinstance(summary, dict) else str(summary.get("summary", ""))
    bullets = bulletize(summary_text)
    raw_facts = summary.get("facts") if isinstance(summary, dict) else None
    fact_lines = tidy_lines(raw_facts or []) if isinstance(raw_facts, list) else []

    content += "## Snapshot\n\n"
    if bullets:
        for line in bullets:
            content += f"- {line}\n"
    else:
        content += "- No summary captured yet.\n"

    if fact_lines and fact_lines != bullets:
        content += "\n## Key facts\n\n"
        for fact in fact_lines:
            content += f"- {fact}\n"

    if entries:
        content += "\n## Recent notes\n\n"
        for entry in entries:
            alias = entry["alias"]
            if entry["keywords_line"]:
                detail = f"keywords: {entry['keywords_line']}"
            else:
                detail = entry["quick_summary"]
            content += (
                f"- {entry['date']} {entry['time']} • {entry['role']}: [[{entry['link_path']}|{alias}]]"
                f" — {detail}\n"
            )

    content += "\n---\n"
    content += f"Raw notes folder: [[{segment}/|Open {display_segment} notes]]\n"

    summary_path = VAULT / f"{summary_title}.md"
    summary_path.write_text(content, encoding="utf-8")
    return summary_title


def write_raw_notes(
    segment: str,
    summary_title: str,
    entries: List[Dict[str, object]],
) -> None:
    seg_dir = VAULT / segment
    for entry in entries:
        filename = entry["file_stem"] + ".md"
        tags = [f"segment/{segment}", "raw"]
        content = mkfront(entry["note_title"], tags, entry["iso"])
        content += f"**Captured:** {entry['date']} {entry['time']} UTC\n"
        content += f"**Role:** {entry['role']}\n\n"

        content += "## Summary\n\n"
        content += f"- Message: {entry['role']} — {entry['quick_summary']}\n"
        if entry["keywords"]:
            content += f"- Keywords: {', '.join(entry['keywords'][:6])}\n"

        content += "\n## Message\n\n"
        if entry["text"]:
            content += entry["text"] + "\n"
        else:
            content += "_No transcript text stored._\n"

        metadata = entry.get("metadata")
        if isinstance(metadata, dict) and metadata:
            content += "\n## Metadata\n\n"
            for key, value in metadata.items():
                content += f"- **{key}:** {value}\n"

        content += "\n---\n"
        content += f"Segment summary: [[{summary_title}]]\n"
        content += f"Source file: {entry['raw_name']}\n"

        (seg_dir / filename).write_text(content, encoding="utf-8")


def create_index(segments: Iterable[str], summaries: Dict[str, str]) -> None:
    index_path = VAULT / "Index.md"
    content = "# Nova Context Index\n\n"
    if not segments:
        content += "No segments found.\n"
    for segment in segments:
        summary_title = summaries.get(segment)
        display_segment = segment.replace("-", " ").title()
        content += f"## {display_segment}\n\n"
        if summary_title:
            content += f"- [[{summary_title}]]\n\n"
        else:
            content += "- No summary note yet.\n\n"
    index_path.write_text(content, encoding="utf-8")


def main() -> None:
    segments = list_segments()
    print("Scanning context folder:", ROOT)
    create_vault()

    summaries: Dict[str, str] = {}
    for segment in segments:
        summary_title = build_segment_notes(segment)
        if summary_title:
            summaries[segment] = summary_title

    create_index(segments, summaries)
    print("Vault built at:", VAULT.resolve())
    print("Open the vault directory in Obsidian to view the graph.")


if __name__ == "__main__":
    main()
