from greenhouse_api import CANDIDATE_URL_RE, JOB_DASHBOARD_RE, candidate_list_url


class TestCandidateListUrl:
    def test_builds_url(self):
        url = candidate_list_url("3209839")
        assert "plans/3209839/candidates" in url
        assert "hiring_plan_id=3209839" in url
        assert "job_status=open" in url

    def test_different_job_id(self):
        url = candidate_list_url("12345")
        assert "plans/12345/candidates" in url
        assert "hiring_plan_id=12345" in url


class TestCandidateUrlRegex:
    def test_matches_standard_url(self):
        m = CANDIDATE_URL_RE.search("/people/123/applications/456")
        assert m
        assert m.group("person") == "123"
        assert m.group("app") == "456"

    def test_matches_redesign_url(self):
        m = CANDIDATE_URL_RE.search("/people/123/applications/456/redesign")
        assert m
        assert m.group("person") == "123"
        assert m.group("app") == "456"

    def test_no_match_on_unrelated_url(self):
        assert CANDIDATE_URL_RE.search("/jobs/123") is None


class TestJobDashboardRegex:
    def test_matches_sdash(self):
        m = JOB_DASHBOARD_RE.search("/sdash/3209839")
        assert m
        assert m.group(1) == "3209839"

    def test_no_match_on_other_path(self):
        assert JOB_DASHBOARD_RE.search("/dashboard/123") is None
