from pathlib import Path

from pdf_merger import escape, wrap, discover_candidates


class TestWrap:
    def test_short_text(self):
        assert wrap("hello world", 70) == ["hello world"]

    def test_wraps_at_width(self):
        text = "a " * 40
        lines = wrap(text.strip(), 20)
        assert all(len(line) <= 21 for line in lines)
        assert " ".join(lines) == text.strip()

    def test_empty_string(self):
        assert wrap("") == []

    def test_single_long_word(self):
        word = "x" * 100
        assert wrap(word, 20) == [word]


class TestEscape:
    def test_escapes_backslash(self):
        assert escape("a\\b") == "a\\\\b"

    def test_escapes_parens(self):
        assert escape("(hello)") == "\\(hello\\)"

    def test_combined(self):
        assert escape("a\\(b)") == "a\\\\\\(b\\)"

    def test_no_escaping_needed(self):
        assert escape("hello world") == "hello world"


class TestDiscoverCandidates:
    def test_discovers_pdfs(self, tmp_path):
        raw_dir = tmp_path / "raw"
        raw_dir.mkdir()
        (raw_dir / "12345_john-doe.pdf").write_bytes(b"%PDF-1.4")
        (raw_dir / "67890_jane-smith.pdf").write_bytes(b"%PDF-1.4")
        (raw_dir / "readme.txt").write_text("not a pdf")

        records = discover_candidates(raw_dir)
        assert len(records) == 2
        assert records[0]["candidate_id"] == "12345"
        assert records[0]["name"] == "john doe"
        assert records[1]["candidate_id"] == "67890"

    def test_skips_non_numeric_prefix(self, tmp_path):
        raw_dir = tmp_path / "raw"
        raw_dir.mkdir()
        (raw_dir / "abc_test.pdf").write_bytes(b"%PDF-1.4")

        records = discover_candidates(raw_dir)
        assert len(records) == 0

    def test_empty_dir(self, tmp_path):
        raw_dir = tmp_path / "raw"
        raw_dir.mkdir()
        assert discover_candidates(raw_dir) == []
