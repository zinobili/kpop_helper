import pytest

from app import config, glossary


@pytest.fixture(autouse=True)
def isolated_glossary(tmp_path, monkeypatch):
    """Every test gets its own empty glossary.json, never the real project data."""
    monkeypatch.setattr(config, "GLOSSARY_PATH", tmp_path / "glossary.json")


def test_list_entries_empty_when_no_file_yet():
    assert glossary.list_entries() == []


def test_upsert_then_list():
    glossary.upsert_entry(glossary.GlossaryEntry(term="오빌리", translation="OB", notes="stage name"))
    entries = glossary.list_entries()
    assert len(entries) == 1
    assert entries[0].term == "오빌리"
    assert entries[0].translation == "OB"


def test_upsert_is_case_insensitive_update_not_duplicate():
    glossary.upsert_entry(glossary.GlossaryEntry(term="Aespa", translation="A"))
    glossary.upsert_entry(glossary.GlossaryEntry(term="aespa", translation="B"))
    entries = glossary.list_entries()
    assert len(entries) == 1
    assert entries[0].translation == "B"


def test_delete_entry_returns_true_when_found_false_otherwise():
    glossary.upsert_entry(glossary.GlossaryEntry(term="term", translation="t"))
    assert glossary.delete_entry("TERM") is True  # case-insensitive
    assert glossary.list_entries() == []
    assert glossary.delete_entry("term") is False


def test_format_for_prompt_reflects_entries():
    assert "no glossary entries" in glossary.format_for_prompt()
    glossary.upsert_entry(glossary.GlossaryEntry(term="A", translation="B", notes="note"))
    formatted = glossary.format_for_prompt()
    assert "A -> B" in formatted
    assert "note" in formatted
