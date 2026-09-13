import json
from typing import List

from pydantic import BaseModel

from . import config


class GlossaryEntry(BaseModel):
    term: str
    translation: str
    notes: str = ""


def _load_raw() -> List[dict]:
    if not config.GLOSSARY_PATH.exists():
        return []
    with open(config.GLOSSARY_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def _save_raw(entries: List[dict]) -> None:
    with open(config.GLOSSARY_PATH, "w", encoding="utf-8") as f:
        json.dump(entries, f, ensure_ascii=False, indent=2)


def list_entries() -> List[GlossaryEntry]:
    return [GlossaryEntry(**e) for e in _load_raw()]


def upsert_entry(entry: GlossaryEntry) -> None:
    entries = _load_raw()
    for e in entries:
        if e["term"].strip().lower() == entry.term.strip().lower():
            e.update(entry.model_dump())
            _save_raw(entries)
            return
    entries.append(entry.model_dump())
    _save_raw(entries)


def delete_entry(term: str) -> bool:
    entries = _load_raw()
    filtered = [e for e in entries if e["term"].strip().lower() != term.strip().lower()]
    if len(filtered) == len(entries):
        return False
    _save_raw(filtered)
    return True


def format_for_prompt() -> str:
    entries = list_entries()
    if not entries:
        return "(no glossary entries yet)"
    lines = []
    for e in entries:
        line = f"- {e.term} -> {e.translation}"
        if e.notes:
            line += f"  ({e.notes})"
        lines.append(line)
    return "\n".join(lines)
