from models import CrawlResult, slugify


class TestSlugify:
    def test_basic_name(self):
        assert slugify("John Doe") == "john-doe"

    def test_special_chars(self):
        assert slugify("María García-López") == "mar-a-garc-a-l-pez"

    def test_empty_string(self):
        assert slugify("") == "unnamed"

    def test_only_special_chars(self):
        assert slugify("!!!") == "unnamed"

    def test_trailing_leading_hyphens(self):
        assert slugify("  John  ") == "john"

    def test_multiple_spaces(self):
        assert slugify("John   Middle   Doe") == "john-middle-doe"

    def test_numbers_preserved(self):
        assert slugify("Candidate 42") == "candidate-42"


class TestCrawlResultJson:
    def test_to_json_roundtrip(self):
        result = CrawlResult(
            candidate_id="123",
            name="Test User",
            status="ok",
            detail_url="https://example.com/123",
            pdf_path="/output/123_test-user.pdf",
            source_url="https://s3.example.com/file.pdf",
            bytes=1024,
        )
        d = result.to_json()
        assert d["candidate_id"] == "123"
        assert d["name"] == "Test User"
        assert d["status"] == "ok"
        assert d["pdf_file"] == "/output/123_test-user.pdf"
        assert d["bytes"] == 1024
        assert d["error"] == ""

    def test_to_json_error_case(self):
        result = CrawlResult(
            candidate_id="456",
            name="",
            status="error",
            error="timeout",
        )
        d = result.to_json()
        assert d["status"] == "error"
        assert d["error"] == "timeout"
        assert d["pdf_file"] is None
