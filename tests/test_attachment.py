from attachment import collect_attachments, pick_resume_id_from_captures, unwrap_preview_source


class TestUnwrapPreviewSource:
    def test_extracts_document_url(self):
        url = "https://app.greenhouse.io/viewer?document_url=https%3A%2F%2Fs3.amazonaws.com%2Fresume.pdf&other=1"
        assert unwrap_preview_source(url) == "https://s3.amazonaws.com/resume.pdf"

    def test_returns_original_if_no_document_url(self):
        url = "https://s3.amazonaws.com/resume.pdf"
        assert unwrap_preview_source(url) == url

    def test_empty_string(self):
        assert unwrap_preview_source("") == ""

    def test_none_like(self):
        assert unwrap_preview_source("") == ""


class TestCollectAttachments:
    def test_finds_attachment_in_flat_dict(self):
        data = {"id": "42", "type": "resume", "filename": "cv.pdf", "url": "https://example.com/cv.pdf"}
        out = []
        collect_attachments(data, out, "https://api.example.com")
        assert len(out) == 1
        assert out[0]["id"] == "42"
        assert out[0]["type"] == "resume"

    def test_finds_nested_attachment(self):
        data = {
            "candidate": {
                "attachments": [
                    {"id": "1", "type": "resume", "filename": "r.pdf"},
                    {"id": "2", "type": "cover_letter", "filename": "cl.pdf"},
                ]
            }
        }
        out = []
        collect_attachments(data, out, "")
        assert len(out) == 2

    def test_no_attachments(self):
        data = {"name": "John", "email": "john@example.com"}
        out = []
        collect_attachments(data, out, "")
        assert len(out) == 0

    def test_uses_attachment_id_key(self):
        data = {"attachment_id": "99", "name": "resume.pdf"}
        out = []
        collect_attachments(data, out, "")
        assert len(out) == 1
        assert out[0]["id"] == "99"

    def test_uses_attachmentId_camel_case(self):
        data = {"attachmentId": "77", "filename": "cv.docx"}
        out = []
        collect_attachments(data, out, "")
        assert len(out) == 1
        assert out[0]["id"] == "77"


class TestPickResumeId:
    def test_prefers_resume_type(self):
        captured = [
            {"id": "1", "type": "cover_letter", "filename": "cl.pdf"},
            {"id": "2", "type": "resume", "filename": "resume.pdf"},
            {"id": "3", "type": "other", "filename": "other.pdf"},
        ]
        assert pick_resume_id_from_captures(captured) == "2"

    def test_prefers_pdf_over_docx(self):
        captured = [
            {"id": "1", "type": "other", "filename": "resume.docx"},
            {"id": "2", "type": "other", "filename": "resume.pdf"},
        ]
        assert pick_resume_id_from_captures(captured) == "2"

    def test_returns_none_for_empty(self):
        assert pick_resume_id_from_captures([]) is None

    def test_falls_back_to_any(self):
        captured = [{"id": "5", "type": "unknown", "filename": "something.txt"}]
        assert pick_resume_id_from_captures(captured) == "5"
