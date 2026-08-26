import csv
import os
import sys
import time
from datetime import datetime, timezone

import httpx
import pandas as pd

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from unittest.mock import patch

import pytest
from click.testing import CliRunner
from pytest_httpx import HTTPXMock  # Import HTTPXMock for type hinting

from stargazers.cli import (
    MAX_RATE_LIMIT_RETRIES,
    cli,
    fetch_commits,
    fetch_contributors,
    fetch_forkers,
    fetch_issues,
    fetch_releases,
    fetch_stargazers,
    fetch_traffic_clones,
    fetch_traffic_referrers,
    fetch_traffic_views,
    fetch_user_metadata,
    fetch_user_repos,
    summarize_and_save,
)


# Use httpx_mock for mocking HTTP requests
@pytest.fixture(autouse=True)
def patch_console_for_tests(monkeypatch):
    # Patch rich.console.Console methods to avoid actual printing during tests
    class DummyConsole:
        def log(self, *args, **kwargs):
            pass

        def print(self, *args, **kwargs):
            pass

        def status(self, *args, **kwargs):
            # Return a dummy context manager for `with console.status(...)`
            class DummyStatus:
                def __enter__(self):
                    return self

                def __exit__(self, exc_type, exc_val, exc_tb):
                    pass

            return DummyStatus()

    monkeypatch.setattr("stargazers.cli.console", DummyConsole())

    # Also patch rich.progress.track as it prints to console
    def dummy_track(iterable, description=""):
        yield from iterable

    monkeypatch.setattr("stargazers.cli.track", dummy_track)


@pytest.fixture
def runner():
    return CliRunner()


@pytest.fixture
def httpx_mock_non_strict_assertion(httpx_mock: HTTPXMock):
    httpx_mock.assert_all_responses_were_requested = False
    yield httpx_mock


def test_cli_does_not_log_token_and_warns_when_unset(monkeypatch):
    messages = []

    class RecordingConsole:
        def log(self, message):
            messages.append(message)

    monkeypatch.setattr("stargazers.cli.console", RecordingConsole())
    monkeypatch.setenv("GITHUB_TOKEN", "secret-token-value")
    cli.callback()

    assert messages == []

    monkeypatch.delenv("GITHUB_TOKEN")
    cli.callback()

    assert messages == ["[yellow]Warning: GITHUB_TOKEN not set. You may hit rate limits quickly.[/]"]


def test_fetch_stargazers(httpx_mock_non_strict_assertion):
    httpx_mock = httpx_mock_non_strict_assertion  # Use the yielded mock
    repo = "test_owner/test_repo"
    stargazers_page1 = [
        {"user": {"login": "user1", "id": 1}, "starred_at": "2023-01-01T00:00:00Z"},
        {"user": {"login": "user2", "id": 2}, "starred_at": "2023-01-02T00:00:00Z"},
    ]
    base_url = f"https://api.github.com/repos/{repo}/stargazers"
    # First page
    httpx_mock.add_response(
        url=f"{base_url}?per_page={PER_PAGE}&page=1",
        method="GET",
        match_headers={"Accept": "application/vnd.github.v3.star+json"},
        json=stargazers_page1,
        status_code=200,
    )
    users, complete = fetch_stargazers(repo)
    assert complete is True
    assert len(users) == 2
    assert users[0]["login"] == "user1"
    assert users[1]["login"] == "user2"
    assert users[0]["starred_at"] == "2023-01-01T00:00:00Z"


def test_fetch_user_metadata(httpx_mock):
    stargazers_input = [
        {"login": "user1", "starred_at": "2023-01-01T00:00:00Z", "user_details": None},
        {"login": "user2", "starred_at": "2023-01-02T00:00:00Z", "user_details": None},
    ]
    user1_data = {
        "login": "user1",
        "name": "User One",
        "company": "TestCo",
        "location": "Earth",
        "email": "user1@example.com",
        "bio": "Bio1",
        "followers": 10,
        "public_repos": 5,
    }
    user2_data = {
        "login": "user2",
        "name": "User Two",
        "company": None,
        "location": "Mars",
        "email": None,
        "bio": None,
        "followers": 20,
        "public_repos": 8,
    }
    httpx_mock.add_response(
        url="https://api.github.com/users/user1",
        method="GET",
        json=user1_data,
        status_code=200,
    )
    httpx_mock.add_response(
        url="https://api.github.com/users/user2",
        method="GET",
        json=user2_data,
        status_code=200,
    )
    metadata = fetch_user_metadata(stargazers_input)
    assert len(metadata) == 2
    assert metadata[0]["login"] == "user1"
    assert metadata[1]["login"] == "user2"
    assert metadata[0]["starred_at"] == "2023-01-01T00:00:00Z"


def test_fetch_user_repos_matches_owner_case_insensitively(httpx_mock):
    httpx_mock.add_response(
        url="https://api.github.com/users/WDM0006/repos?type=owner&sort=full_name&per_page=100&page=1",
        json=[
            {"full_name": "wdm0006/first", "owner": {"login": "wdm0006"}},
            {"full_name": "wdm0006/second", "owner": {"login": "wdm0006"}},
        ],
    )

    assert fetch_user_repos("WDM0006") == ["wdm0006/first", "wdm0006/second"]


def test_fetch_user_repos_excludes_different_owner(httpx_mock):
    httpx_mock.add_response(
        url="https://api.github.com/users/WDM0006/repos?type=owner&sort=full_name&per_page=100&page=1",
        json=[
            {"full_name": "wdm0006/owned", "owner": {"login": "wdm0006"}},
            {"full_name": "someone-else/not-owned", "owner": {"login": "someone-else"}},
        ],
    )

    assert fetch_user_repos("WDM0006") == ["wdm0006/owned"]


def test_fetch_forkers(httpx_mock_non_strict_assertion):
    httpx_mock = httpx_mock_non_strict_assertion  # Use the yielded mock
    repo = "test_owner/test_repo"
    # Mock two pages of forkers
    forkers_page1 = [
        {"owner": {"login": "forker1", "id": 101}, "created_at": "2023-01-01T00:00:00Z"},
        {"owner": {"login": "forkerB", "id": 102}, "created_at": "2023-01-02T00:00:00Z"},
    ]
    base_url = f"https://api.github.com/repos/{repo}/forks"
    # First page
    httpx_mock.add_response(
        url=f"{base_url}?per_page={PER_PAGE}&page=1",
        method="GET",
        json=forkers_page1,
        status_code=200,
    )
    users, complete = fetch_forkers(repo)
    assert complete is True
    assert len(users) == 2
    assert users[0]["login"] == "forker1"
    assert users[1]["login"] == "forkerB"
    assert users[0]["forked_at"] == "2023-01-01T00:00:00Z"


class CapturingConsole:
    """Console stub that records log() messages so tests can assert on warnings."""

    def __init__(self):
        self.messages = []

    def log(self, *args, **kwargs):
        self.messages.append(" ".join(str(a) for a in args))

    def print(self, *args, **kwargs):
        self.messages.append(" ".join(str(a) for a in args))

    def status(self, *args, **kwargs):
        class DummyStatus:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc_val, exc_tb):
                pass

        return DummyStatus()


def test_fetch_stargazers_incomplete_on_request_error(httpx_mock_non_strict_assertion, monkeypatch):
    """A network error after the first page returns partial data flagged incomplete + warns."""
    httpx_mock = httpx_mock_non_strict_assertion
    capturing = CapturingConsole()
    monkeypatch.setattr("stargazers.cli.console", capturing)

    repo = "test_owner/test_repo"
    base_url = f"https://api.github.com/repos/{repo}/stargazers"
    # First page succeeds and advertises a next page via the Link header.
    httpx_mock.add_response(
        url=f"{base_url}?per_page={PER_PAGE}&page=1",
        method="GET",
        match_headers={"Accept": "application/vnd.github.v3.star+json"},
        json=[{"user": {"login": "user1", "id": 1}, "starred_at": "2023-01-01T00:00:00Z"}],
        status_code=200,
        headers={"Link": f'<{base_url}?per_page={PER_PAGE}&page=2>; rel="next"'},
    )
    # Second page raises a network error mid-pagination.
    httpx_mock.add_exception(httpx.ConnectError("boom"))

    users, complete = fetch_stargazers(repo)

    # Partial data is returned (page 1 only) and explicitly flagged incomplete.
    assert complete is False
    assert len(users) == 1
    assert users[0]["login"] == "user1"
    # A prominent, distinguishable warning is emitted.
    assert any("WARNING: incomplete data" in m and repo in m for m in capturing.messages)


@pytest.fixture
def no_sleep(monkeypatch):
    """Skip the rate-limit waits so retry-cap tests run instantly."""
    monkeypatch.setattr("stargazers.cli.time.sleep", lambda _seconds: None)


def test_fetch_stargazers_caps_rate_limit_retries(httpx_mock_non_strict_assertion, monkeypatch, no_sleep):
    """A persistent rate-limit 403 stops after the cap and returns partial data."""
    httpx_mock = httpx_mock_non_strict_assertion
    capturing = CapturingConsole()
    monkeypatch.setattr("stargazers.cli.console", capturing)

    repo = "test_owner/test_repo"
    base_url = f"https://api.github.com/repos/{repo}/stargazers"
    # First page succeeds and advertises a next page via the Link header.
    httpx_mock.add_response(
        url=f"{base_url}?per_page={PER_PAGE}&page=1",
        method="GET",
        json=[{"user": {"login": "user1", "id": 1}, "starred_at": "2023-01-01T00:00:00Z"}],
        status_code=200,
        headers={"Link": f'<{base_url}?per_page={PER_PAGE}&page=2>; rel="next"'},
    )
    # Page 2 is rate limited forever.
    httpx_mock.add_response(
        url=f"{base_url}?per_page={PER_PAGE}&page=2",
        method="GET",
        status_code=403,
        text="API rate limit exceeded",
        is_reusable=True,
    )

    users, complete = fetch_stargazers(repo)

    assert complete is False
    assert len(users) == 1
    assert users[0]["login"] == "user1"
    # Page 2 was attempted exactly MAX_RATE_LIMIT_RETRIES + 1 times, then abandoned.
    page2_requests = [r for r in httpx_mock.get_requests() if "page=2" in str(r.url)]
    assert len(page2_requests) == MAX_RATE_LIMIT_RETRIES + 1
    assert any("WARNING: incomplete data" in m and repo in m for m in capturing.messages)


def test_fetch_forkers_caps_rate_limit_retries(httpx_mock_non_strict_assertion, monkeypatch, no_sleep):
    """A persistent rate-limit 403 stops after the cap and returns partial data."""
    httpx_mock = httpx_mock_non_strict_assertion
    capturing = CapturingConsole()
    monkeypatch.setattr("stargazers.cli.console", capturing)

    repo = "test_owner/test_repo"
    base_url = f"https://api.github.com/repos/{repo}/forks"
    httpx_mock.add_response(
        url=f"{base_url}?per_page={PER_PAGE}&page=1",
        method="GET",
        json=[{"owner": {"login": "forker1", "id": 101}, "created_at": "2023-01-01T00:00:00Z"}],
        status_code=200,
        headers={"Link": f'<{base_url}?per_page={PER_PAGE}&page=2>; rel="next"'},
    )
    httpx_mock.add_response(
        url=f"{base_url}?per_page={PER_PAGE}&page=2",
        method="GET",
        status_code=403,
        text="API rate limit exceeded",
        is_reusable=True,
    )

    forkers, complete = fetch_forkers(repo)

    assert complete is False
    assert len(forkers) == 1
    assert forkers[0]["login"] == "forker1"
    page2_requests = [r for r in httpx_mock.get_requests() if "page=2" in str(r.url)]
    assert len(page2_requests) == MAX_RATE_LIMIT_RETRIES + 1
    assert any("WARNING: incomplete data" in m and repo in m for m in capturing.messages)


def test_fetch_user_repos_caps_rate_limit_retries(httpx_mock_non_strict_assertion, monkeypatch, no_sleep):
    """A persistent rate-limit 403 exits rather than retrying the same page forever."""
    httpx_mock = httpx_mock_non_strict_assertion
    capturing = CapturingConsole()
    monkeypatch.setattr("stargazers.cli.console", capturing)

    httpx_mock.add_response(
        url=f"{BASE_API_URL}/users/testuser/repos?type=owner&sort=full_name&per_page={PER_PAGE}&page=1",
        method="GET",
        status_code=403,
        text="API rate limit exceeded",
        is_reusable=True,
    )

    with pytest.raises(SystemExit):
        fetch_user_repos("testuser")

    assert len(httpx_mock.get_requests()) == MAX_RATE_LIMIT_RETRIES + 1
    assert any("Giving up fetching repos for testuser" in m for m in capturing.messages)


def test_fetch_stargazers_recovers_from_transient_rate_limit(httpx_mock_non_strict_assertion, monkeypatch, no_sleep):
    """Retries below the cap still succeed, and the counter resets per page."""
    httpx_mock = httpx_mock_non_strict_assertion
    monkeypatch.setattr("stargazers.cli.console", CapturingConsole())

    repo = "test_owner/test_repo"
    base_url = f"https://api.github.com/repos/{repo}/stargazers"
    httpx_mock.add_response(
        url=f"{base_url}?per_page={PER_PAGE}&page=1",
        method="GET",
        status_code=403,
        text="API rate limit exceeded",
    )
    httpx_mock.add_response(
        url=f"{base_url}?per_page={PER_PAGE}&page=1",
        method="GET",
        json=[{"user": {"login": "user1", "id": 1}, "starred_at": "2023-01-01T00:00:00Z"}],
        status_code=200,
    )

    users, complete = fetch_stargazers(repo)

    assert complete is True
    assert [u["login"] for u in users] == ["user1"]


@pytest.fixture
def recorded_sleeps(monkeypatch):
    """Record every time.sleep() duration instead of actually sleeping."""
    sleeps = []
    monkeypatch.setattr("stargazers.cli.time.sleep", sleeps.append)
    return sleeps


def test_fetch_user_metadata_retries_rate_limit_without_extra_sleep(httpx_mock, recorded_sleeps):
    """A rate-limited user is retried using only _handle_api_error's reset-time wait."""
    reset_at = int(time.time()) + 5
    httpx_mock.add_response(
        url="https://api.github.com/users/user1",
        method="GET",
        status_code=403,
        text="API rate limit exceeded",
        headers={"X-RateLimit-Reset": str(reset_at)},
    )
    httpx_mock.add_response(
        url="https://api.github.com/users/user1",
        method="GET",
        json={
            "login": "user1",
            "name": "User One",
            "company": "TestCo",
            "location": "Earth",
            "email": "user1@example.com",
            "bio": "Bio1",
            "followers": 10,
            "public_repos": 5,
        },
        status_code=200,
    )

    metadata = fetch_user_metadata([{"login": "user1", "starred_at": "2023-01-01T00:00:00Z", "user_details": None}])

    # The retry succeeded and the user was enriched.
    assert len(httpx_mock.get_requests()) == 2
    assert len(metadata) == 1
    assert metadata[0]["login"] == "user1"
    assert metadata[0]["location"] == "Earth"
    assert metadata[0]["followers"] == 10
    assert metadata[0]["starred_at"] == "2023-01-01T00:00:00Z"

    # Exactly two sleeps: the reset-time wait, then the 0.1s post-success pause.
    # The old redundant fixed 60s wait after the "retry" signal is gone.
    assert recorded_sleeps == [pytest.approx(reset_at - int(time.time()), abs=1), 0.1]
    assert 60 not in recorded_sleeps


def test_fetch_user_metadata_caps_rate_limit_retries(httpx_mock_non_strict_assertion, recorded_sleeps):
    """A persistent rate limit gives up after max_retries attempts and skips the user."""
    httpx_mock = httpx_mock_non_strict_assertion
    reset_at = int(time.time()) + 5
    httpx_mock.add_response(
        url="https://api.github.com/users/user1",
        method="GET",
        status_code=403,
        text="API rate limit exceeded",
        headers={"X-RateLimit-Reset": str(reset_at)},
        is_reusable=True,
    )

    metadata = fetch_user_metadata([{"login": "user1", "starred_at": "2023-01-01T00:00:00Z", "user_details": None}])

    assert metadata == []
    # max_retries = 3 attempts, each waiting once inside _handle_api_error and no more.
    assert len(httpx_mock.get_requests()) == 3
    assert len(recorded_sleeps) == 3
    assert 60 not in recorded_sleeps


GOOD_USER_PROFILE = {
    "login": "gooduser",
    "name": "Good User",
    "company": "GoodCo",
    "location": "Reykjavik",
    "email": "good@example.com",
    "bio": "Still here",
    "followers": 42,
    "public_repos": 7,
}


def _two_star_events():
    return [
        {"login": "ghostuser", "starred_at": "2023-01-01T00:00:00Z", "user_details": None},
        {"login": "gooduser", "starred_at": "2023-01-02T00:00:00Z", "user_details": None},
    ]


def _assert_only_good_user(metadata):
    """The surviving row must carry gooduser's real profile values, not just the right shape."""
    assert [u["login"] for u in metadata] == ["gooduser"]
    assert metadata[0]["name"] == "Good User"
    assert metadata[0]["location"] == "Reykjavik"
    assert metadata[0]["followers"] == 42
    assert metadata[0]["public_repos"] == 7
    assert metadata[0]["starred_at"] == "2023-01-02T00:00:00Z"


@pytest.mark.parametrize(
    ("status_code", "body"),
    [(404, "Not Found"), (500, "Internal Server Error")],
)
def test_fetch_user_metadata_skips_unfetchable_user(httpx_mock, recorded_sleeps, status_code, body):
    """A user whose profile cannot be fetched is skipped; the remaining users are still enriched."""
    httpx_mock.add_response(
        url="https://api.github.com/users/ghostuser",
        method="GET",
        status_code=status_code,
        text=body,
    )
    httpx_mock.add_response(
        url="https://api.github.com/users/gooduser",
        method="GET",
        json=GOOD_USER_PROFILE,
        status_code=200,
    )

    metadata = fetch_user_metadata(_two_star_events())

    _assert_only_good_user(metadata)
    # The bad user is skipped after a single attempt and the good user is still requested.
    assert [str(r.url) for r in httpx_mock.get_requests()] == [
        "https://api.github.com/users/ghostuser",
        "https://api.github.com/users/gooduser",
    ]


def test_fetch_user_metadata_logs_skipped_count(httpx_mock, monkeypatch):
    """The number of skipped users is surfaced in a summary log line."""
    capturing = CapturingConsole()
    monkeypatch.setattr("stargazers.cli.console", capturing)
    httpx_mock.add_response(
        url="https://api.github.com/users/ghostuser",
        method="GET",
        status_code=404,
        text="Not Found",
    )
    httpx_mock.add_response(
        url="https://api.github.com/users/gooduser",
        method="GET",
        json=GOOD_USER_PROFILE,
        status_code=200,
    )

    metadata = fetch_user_metadata(_two_star_events())

    _assert_only_good_user(metadata)
    assert any("Skipping user ghostuser due to error: 404" in message for message in capturing.messages)
    assert any("Skipped 1 user(s) whose profile could not be fetched." in message for message in capturing.messages)


BASE_API_URL = "https://api.github.com"
PER_PAGE = 100  # Define for clarity in mock helpers


def mock_user_repos_api(httpx_mock, username, repos_data):
    """Helper to mock the /users/{username}/repos endpoint."""
    url_page1 = f"{BASE_API_URL}/users/{username}/repos?type=owner&sort=full_name&per_page={PER_PAGE}&page=1"
    httpx_mock.add_response(url=url_page1, method="GET", json=repos_data, status_code=200)
    # Only mock page 2 if page 1 returned a full page of results
    if len(repos_data) == PER_PAGE:
        url_page2 = f"{BASE_API_URL}/users/{username}/repos?type=owner&sort=full_name&per_page={PER_PAGE}&page=2"
        httpx_mock.add_response(url=url_page2, method="GET", json=[], status_code=200)


def mock_stargazers_api(httpx_mock, repo_full_name, stargazers_event_data):
    """Helper to mock the /repos/{repo_full_name}/stargazers endpoint."""
    page_data = []
    for i, event_data in enumerate(stargazers_event_data):
        user_detail = {"login": event_data.get("login", f"test_sg_user{i}"), "id": i + 1000}
        starred_at_val = event_data.get("starred_at", f"2023-01-{i + 1:02d}T10:00:00Z")
        page_data.append({"user": user_detail, "starred_at": starred_at_val})

    url_page1 = f"{BASE_API_URL}/repos/{repo_full_name}/stargazers?per_page={PER_PAGE}&page=1"
    httpx_mock.add_response(
        url=url_page1,
        method="GET",
        json=page_data,
        status_code=200,
        match_headers={"Accept": "application/vnd.github.v3.star+json"},
    )
    # Only mock page 2 if page 1 returned a full page of results
    if len(page_data) == PER_PAGE:
        url_page2 = f"{BASE_API_URL}/repos/{repo_full_name}/stargazers?per_page={PER_PAGE}&page=2"
        httpx_mock.add_response(
            url=url_page2,
            method="GET",
            json=[],
            status_code=200,
            match_headers={"Accept": "application/vnd.github.v3.star+json"},
        )


def mock_forkers_api(httpx_mock, repo_full_name, forkers_event_data):
    """Helper to mock the /repos/{repo_full_name}/forks endpoint."""
    page_data = []
    for i, event_data in enumerate(forkers_event_data):
        owner_detail = {"login": event_data.get("login", f"test_fork_user{i}"), "id": i + 2000}
        created_at_val = event_data.get("created_at", event_data.get("forked_at", f"2023-01-{i + 1:02d}T11:00:00Z"))
        page_data.append({"owner": owner_detail, "created_at": created_at_val})

    url_page1 = f"{BASE_API_URL}/repos/{repo_full_name}/forks?per_page={PER_PAGE}&page=1"
    httpx_mock.add_response(url=url_page1, method="GET", json=page_data, status_code=200)
    # Only mock page 2 if page 1 returned a full page of results
    if len(page_data) == PER_PAGE:
        url_page2 = f"{BASE_API_URL}/repos/{repo_full_name}/forks?per_page={PER_PAGE}&page=2"
        httpx_mock.add_response(url=url_page2, method="GET", json=[], status_code=200)


def read_csv_output(file_path):
    """Reads the CSV output into a list of dictionaries."""
    with open(file_path, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        return list(reader)


def test_account_trend_basic(runner, httpx_mock_non_strict_assertion, tmp_path, monkeypatch):
    httpx_mock = httpx_mock_non_strict_assertion  # Use the yielded mock
    username = "testuser"
    monkeypatch.chdir(tmp_path)  # Changed from tmp_path.as_cwd()
    mock_user_repos_api(
        httpx_mock,
        username,
        [
            {"full_name": "testuser/repo1", "owner": {"login": username}},
            {"full_name": "testuser/repo2", "owner": {"login": username}},
        ],
    )
    mock_stargazers_api(
        httpx_mock,
        "testuser/repo1",
        [
            {"login": "sg1", "starred_at": "2023-01-01T10:00:00Z"},
            {"login": "sg2", "starred_at": "2023-01-01T12:00:00Z"},
        ],
    )
    mock_stargazers_api(httpx_mock, "testuser/repo2", [{"login": "sg3", "starred_at": "2023-01-02T10:00:00Z"}])
    result = runner.invoke(cli, ["account-trend", username], catch_exceptions=False)
    assert result.exit_code == 0, f"CLI Error: {result.output}"
    output_file = tmp_path / f"{username}_account_stars_by_day.csv"
    assert output_file.exists()
    data = sorted(read_csv_output(output_file), key=lambda x: x["star_date"], reverse=True)
    assert len(data) == 2
    # Check total stats
    assert data[0]["star_date"] == "2023-01-02"
    assert data[0]["total_new_stars_on_day"] == "1"
    assert data[0]["total_cumulative_stars_up_to_day"] == "3"
    assert data[1]["star_date"] == "2023-01-01"
    assert data[1]["total_new_stars_on_day"] == "2"
    assert data[1]["total_cumulative_stars_up_to_day"] == "2"

    # Check per-repo stats
    assert data[0]["testuser_repo1_new_stars"] == "0"
    assert data[0]["testuser_repo1_cumulative_stars"] == "2"
    assert data[0]["testuser_repo2_new_stars"] == "1"
    assert data[0]["testuser_repo2_cumulative_stars"] == "1"
    assert data[1]["testuser_repo1_new_stars"] == "2"
    assert data[1]["testuser_repo1_cumulative_stars"] == "2"
    assert data[1]["testuser_repo2_new_stars"] == "0"
    assert data[1]["testuser_repo2_cumulative_stars"] == "0"


def test_account_trend_warns_on_incomplete_data(runner, httpx_mock_non_strict_assertion, tmp_path, monkeypatch):
    """account-trend still saves partial data but surfaces a prominent warning first."""
    httpx_mock = httpx_mock_non_strict_assertion
    username = "testuser"
    monkeypatch.chdir(tmp_path)
    capturing = CapturingConsole()
    monkeypatch.setattr("stargazers.cli.console", capturing)

    mock_user_repos_api(httpx_mock, username, [{"full_name": "testuser/repo1", "owner": {"login": username}}])

    star_url = f"{BASE_API_URL}/repos/testuser/repo1/stargazers"
    httpx_mock.add_response(
        url=f"{star_url}?per_page={PER_PAGE}&page=1",
        method="GET",
        match_headers={"Accept": "application/vnd.github.v3.star+json"},
        json=[{"user": {"login": "sg1", "id": 1}, "starred_at": "2023-01-01T10:00:00Z"}],
        status_code=200,
        headers={"Link": f'<{star_url}?per_page={PER_PAGE}&page=2>; rel="next"'},
    )
    # Network error on page 2 → partial data for this repo.
    httpx_mock.add_exception(httpx.ConnectError("boom"))

    result = runner.invoke(cli, ["account-trend", username], catch_exceptions=False)
    assert result.exit_code == 0, f"CLI Error: {result.output}"

    # The partial CSV is still written (documented behavior: partial data is saved)...
    output_file = tmp_path / f"{username}_account_stars_by_day.csv"
    assert output_file.exists()
    # ...but the user is prominently warned it may undercount.
    assert any("WARNING: star data was incomplete" in m and "testuser/repo1" in m for m in capturing.messages)


def test_account_trend_skips_missing_repo(runner, httpx_mock_non_strict_assertion, tmp_path, monkeypatch):
    httpx_mock = httpx_mock_non_strict_assertion
    username = "testuser"
    monkeypatch.chdir(tmp_path)
    capturing = CapturingConsole()
    monkeypatch.setattr("stargazers.cli.console", capturing)

    mock_user_repos_api(httpx_mock, username, [{"full_name": "testuser/repo1", "owner": {"login": username}}])
    mock_stargazers_api(
        httpx_mock,
        "testuser/repo1",
        [{"login": "sg1", "starred_at": "2023-01-01T10:00:00Z"}],
    )
    missing_repo = "external/does-not-exist"
    httpx_mock.add_response(
        url=f"{BASE_API_URL}/repos/{missing_repo}/stargazers?per_page={PER_PAGE}&page=1",
        method="GET",
        status_code=404,
        json={"message": "Not Found"},
    )

    result = runner.invoke(cli, ["account-trend", username, "--include-repo", missing_repo], catch_exceptions=False)
    assert result.exit_code == 0

    data = read_csv_output(tmp_path / f"{username}_account_stars_by_day.csv")
    assert data == [
        {
            "star_date": "2023-01-01",
            "total_new_stars_on_day": "1",
            "total_cumulative_stars_up_to_day": "1",
            "testuser_repo1_new_stars": "1",
            "testuser_repo1_cumulative_stars": "1",
        }
    ]
    assert any("WARNING: star data was incomplete" in m and missing_repo in m for m in capturing.messages)


def test_account_trend_exclude_repo(runner, httpx_mock_non_strict_assertion, tmp_path, monkeypatch):
    httpx_mock = httpx_mock_non_strict_assertion  # Use the yielded mock
    username = "testuser"
    monkeypatch.chdir(tmp_path)  # Changed
    mock_user_repos_api(
        httpx_mock,
        username,
        [
            {"full_name": "testuser/repo1", "owner": {"login": username}},
            {"full_name": "testuser/repo2", "owner": {"login": username}},
            {"full_name": "testuser/repo3", "owner": {"login": username}},
        ],
    )
    mock_stargazers_api(httpx_mock, "testuser/repo1", [{"starred_at": "2023-01-01T00:00:00Z"}])
    mock_stargazers_api(httpx_mock, "testuser/repo3", [{"starred_at": "2023-01-02T00:00:00Z"}])
    result = runner.invoke(cli, ["account-trend", username, "--exclude-repo", "testuser/repo2"], catch_exceptions=False)
    assert result.exit_code == 0, f"CLI Error: {result.output}"
    output_file = tmp_path / f"{username}_account_stars_by_day.csv"
    assert output_file.exists()
    data = sorted(read_csv_output(output_file), key=lambda x: x["star_date"], reverse=True)
    assert len(data) == 2
    # Check total stats
    assert data[0]["star_date"] == "2023-01-02"
    assert data[0]["total_new_stars_on_day"] == "1"
    assert data[0]["total_cumulative_stars_up_to_day"] == "2"
    assert data[1]["star_date"] == "2023-01-01"
    assert data[1]["total_new_stars_on_day"] == "1"
    assert data[1]["total_cumulative_stars_up_to_day"] == "1"

    # Check per-repo stats
    assert data[0]["testuser_repo1_new_stars"] == "0"
    assert data[0]["testuser_repo1_cumulative_stars"] == "1"
    assert data[0]["testuser_repo3_new_stars"] == "1"
    assert data[0]["testuser_repo3_cumulative_stars"] == "1"
    assert data[1]["testuser_repo1_new_stars"] == "1"
    assert data[1]["testuser_repo1_cumulative_stars"] == "1"
    assert data[1]["testuser_repo3_new_stars"] == "0"
    assert data[1]["testuser_repo3_cumulative_stars"] == "0"


def test_account_trend_include_repo(runner, httpx_mock_non_strict_assertion, tmp_path, monkeypatch):
    httpx_mock = httpx_mock_non_strict_assertion  # Use the yielded mock
    username = "testuser"
    monkeypatch.chdir(tmp_path)  # Changed
    mock_user_repos_api(httpx_mock, username, [{"full_name": "testuser/owned_repo", "owner": {"login": username}}])
    mock_stargazers_api(httpx_mock, "testuser/owned_repo", [{"starred_at": "2023-02-01T00:00:00Z"}])
    mock_stargazers_api(httpx_mock, "external/another_repo", [{"starred_at": "2023-02-02T00:00:00Z"}])
    result = runner.invoke(
        cli, ["account-trend", username, "--include-repo", "external/another_repo"], catch_exceptions=False
    )
    assert result.exit_code == 0, f"CLI Error: {result.output}"
    output_file = tmp_path / f"{username}_account_stars_by_day.csv"
    assert output_file.exists()
    data = sorted(read_csv_output(output_file), key=lambda x: x["star_date"], reverse=True)
    assert len(data) == 2
    # Check total stats
    assert data[0]["star_date"] == "2023-02-02"
    assert data[0]["total_new_stars_on_day"] == "1"
    assert data[0]["total_cumulative_stars_up_to_day"] == "2"
    assert data[1]["star_date"] == "2023-02-01"
    assert data[1]["total_new_stars_on_day"] == "1"
    assert data[1]["total_cumulative_stars_up_to_day"] == "1"

    # Check per-repo stats
    assert data[0]["testuser_owned_repo_new_stars"] == "0"
    assert data[0]["testuser_owned_repo_cumulative_stars"] == "1"
    assert data[0]["external_another_repo_new_stars"] == "1"
    assert data[0]["external_another_repo_cumulative_stars"] == "1"
    assert data[1]["testuser_owned_repo_new_stars"] == "1"
    assert data[1]["testuser_owned_repo_cumulative_stars"] == "1"
    assert data[1]["external_another_repo_new_stars"] == "0"
    assert data[1]["external_another_repo_cumulative_stars"] == "0"


def test_account_trend_invalid_include_repo(runner, httpx_mock_non_strict_assertion, tmp_path, monkeypatch):
    """A malformed --include-repo is warned about and dropped; owned repos still process."""
    httpx_mock = httpx_mock_non_strict_assertion
    username = "testuser"
    monkeypatch.chdir(tmp_path)
    capturing = CapturingConsole()
    monkeypatch.setattr("stargazers.cli.console", capturing)

    mock_user_repos_api(httpx_mock, username, [{"full_name": "testuser/owned_repo", "owner": {"login": username}}])
    mock_stargazers_api(httpx_mock, "testuser/owned_repo", [{"starred_at": "2023-02-01T00:00:00Z"}])

    # "notarepo" has no slash — it must never reach fetch_stargazers (no mock for it).
    result = runner.invoke(cli, ["account-trend", username, "--include-repo", "notarepo"], catch_exceptions=False)
    assert result.exit_code == 0, f"CLI Error: {result.output}"

    assert any("Invalid repository format: 'notarepo'" in m for m in capturing.messages)

    # The valid owned repo is still processed and the trend CSV is written.
    output_file = tmp_path / f"{username}_account_stars_by_day.csv"
    assert output_file.exists()
    data = read_csv_output(output_file)
    assert len(data) == 1
    assert data[0]["star_date"] == "2023-02-01"
    assert data[0]["total_new_stars_on_day"] == "1"
    assert data[0]["testuser_owned_repo_new_stars"] == "1"


@patch("stargazers.cli.plt")
def test_account_trend_line_chart(mock_plt, runner, httpx_mock_non_strict_assertion, tmp_path, monkeypatch):
    httpx_mock = httpx_mock_non_strict_assertion  # Use the yielded mock
    username = "chartuser"
    monkeypatch.chdir(tmp_path)  # Changed
    mock_user_repos_api(httpx_mock, username, [{"full_name": "chartuser/repoA", "owner": {"login": username}}])
    mock_stargazers_api(
        httpx_mock, "chartuser/repoA", [{"starred_at": "2023-03-01T10:00:00Z"}, {"starred_at": "2023-03-02T10:00:00Z"}]
    )
    result = runner.invoke(cli, ["account-trend", username, "--line-chart"], catch_exceptions=False)
    assert result.exit_code == 0, f"CLI Error: {result.output}"
    output_file = tmp_path / f"{username}_account_stars_by_day.csv"
    assert output_file.exists()
    data = sorted(read_csv_output(output_file), key=lambda x: x["star_date"], reverse=True)
    assert len(data) == 2
    mock_plt.clc.assert_called_once()
    mock_plt.title.assert_called_once_with(f"Cumulative Stars Over Time for {username}")
    mock_plt.xlabel.assert_called_once_with("Days since first star")
    mock_plt.ylabel.assert_called_once_with("Cumulative Stars")
    assert mock_plt.scatter.call_count == 1
    mock_plt.show.assert_called_once()


@patch("stargazers.cli.plt")
def test_account_trend_no_line_chart(mock_plt, runner, httpx_mock_non_strict_assertion, tmp_path, monkeypatch):
    httpx_mock = httpx_mock_non_strict_assertion  # Use the yielded mock
    username = "nochartuser"
    monkeypatch.chdir(tmp_path)  # Changed
    mock_user_repos_api(httpx_mock, username, [{"full_name": "nochartuser/repoB", "owner": {"login": username}}])
    mock_stargazers_api(httpx_mock, "nochartuser/repoB", [{"starred_at": "2023-03-05T00:00:00Z"}])
    result = runner.invoke(cli, ["account-trend", username], catch_exceptions=False)
    assert result.exit_code == 0, f"CLI Error: {result.output}"
    output_file = tmp_path / f"{username}_account_stars_by_day.csv"
    assert output_file.exists()
    mock_plt.plot_date.assert_not_called()
    mock_plt.show.assert_not_called()


@patch("stargazers.cli.plt")
def test_account_trend_line_chart_no_data(mock_plt, runner, httpx_mock_non_strict_assertion, tmp_path, monkeypatch):
    httpx_mock = httpx_mock_non_strict_assertion  # Use the yielded mock
    username = "nodatauser"
    monkeypatch.chdir(tmp_path)  # Changed
    mock_user_repos_api(httpx_mock, username, [{"full_name": "nodatauser/repoC", "owner": {"login": username}}])
    mock_stargazers_api(httpx_mock, "nodatauser/repoC", [])
    result = runner.invoke(cli, ["account-trend", username, "--line-chart"], catch_exceptions=False)
    assert result.exit_code == 0, f"CLI Error: {result.output}"
    output_file = tmp_path / f"{username}_account_stars_by_day.csv"
    assert not output_file.exists()
    mock_plt.plot_date.assert_not_called()


@pytest.fixture
def sample_stargazer_data():
    return [
        {
            "login": "user1",
            "name": "User One",
            "location": "Earth",
            "starred_at": "2023-01-01T00:00:00Z",
            "repo": "test/repo1",
        },
        {
            "login": "user2",
            "name": "User Two",
            "location": "Mars",
            "starred_at": "2023-01-02T00:00:00Z",
            "repo": "test/repo1",
        },
        {
            "login": "user3",
            "name": "User Three",
            "location": "Earth",
            "starred_at": "2023-01-01T05:00:00Z",
            "repo": "test/repo2",
        },
    ]


def test_summarize_and_save_stargazers(tmp_path, sample_stargazer_data, monkeypatch):
    monkeypatch.chdir(tmp_path)  # Ensure file is written to tmp_path
    base_name = "test_owner_test_repo"
    output_suffix = "stargazers"
    timestamp_key = "starred_at"
    # The function summarize_and_save will construct the full path internally if not run via CLI.
    # For a direct call like this, it writes to CWD.
    summarize_and_save(sample_stargazer_data, base_name, output_suffix, timestamp_key)

    expected_output_file = tmp_path / f"{base_name.replace('/', '_')}_{output_suffix}.csv"
    assert expected_output_file.exists()
    data = read_csv_output(expected_output_file)
    assert len(data) == 3
    assert data[0]["login"] == "user2"
    assert data[1]["login"] == "user3"
    assert data[2]["login"] == "user1"


def test_summarize_and_save_account_trend(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)  # Ensure file is written to tmp_path
    trend_data = [
        # Values should be strings as they come from CSV reading in other tests
        {"star_date": "2023-01-01", "new_stars_on_day": "2", "cumulative_stars_up_to_day": "2"},
        {"star_date": "2023-01-02", "new_stars_on_day": "1", "cumulative_stars_up_to_day": "3"},
    ]
    base_name = "testuser"
    output_suffix = "account_stars_by_day"
    timestamp_key = "star_date"
    summarize_and_save(trend_data, base_name, output_suffix, timestamp_key)

    expected_output_file = tmp_path / f"{base_name}_{output_suffix}.csv"
    assert expected_output_file.exists()
    data = read_csv_output(expected_output_file)
    assert len(data) == 2
    assert data[0]["star_date"] == "2023-01-02"
    assert data[0]["new_stars_on_day"] == "1"  # Asserting string value
    assert data[0]["cumulative_stars_up_to_day"] == "3"  # Asserting string value
    assert data[1]["star_date"] == "2023-01-01"
    assert data[1]["new_stars_on_day"] == "2"  # Asserting string value
    assert data[1]["cumulative_stars_up_to_day"] == "2"  # Asserting string value


@pytest.fixture
def underscore_repo_trend_data():
    """Three days of stars for one repo whose name contains an underscore.

    The first day's cumulative total (2) differs from the final one (9), so an
    assertion on the printed final total distinguishes the newest row from the oldest.
    """
    return [
        {
            "star_date": "2023-01-01",
            "total_new_stars_on_day": 2,
            "total_cumulative_stars_up_to_day": 2,
            "wdm0006_my_repo_new_stars": 2,
            "wdm0006_my_repo_cumulative_stars": 2,
        },
        {
            "star_date": "2023-01-02",
            "total_new_stars_on_day": 3,
            "total_cumulative_stars_up_to_day": 5,
            "wdm0006_my_repo_new_stars": 3,
            "wdm0006_my_repo_cumulative_stars": 5,
        },
        {
            "star_date": "2023-01-03",
            "total_new_stars_on_day": 4,
            "total_cumulative_stars_up_to_day": 9,
            "wdm0006_my_repo_new_stars": 4,
            "wdm0006_my_repo_cumulative_stars": 9,
        },
    ]


def test_trend_summary_prints_newest_cumulative_total(tmp_path, monkeypatch, underscore_repo_trend_data):
    """The 'Final cumulative stars' line reports the newest day, not the oldest."""
    monkeypatch.chdir(tmp_path)
    capturing = CapturingConsole()
    monkeypatch.setattr("stargazers.cli.console", capturing)

    summarize_and_save(underscore_repo_trend_data, "testuser", "account_stars_by_day", timestamp_key="star_date")

    assert "Total new stars in period: 9" in capturing.messages
    assert "Final cumulative stars: 9" in capturing.messages
    assert "Final cumulative stars: 2" not in capturing.messages


def test_trend_summary_preserves_underscores_in_repo_names(tmp_path, monkeypatch, underscore_repo_trend_data):
    """Only the first underscore of a repo column prefix is the owner/repo separator."""
    monkeypatch.chdir(tmp_path)
    capturing = CapturingConsole()
    monkeypatch.setattr("stargazers.cli.console", capturing)

    summarize_and_save(underscore_repo_trend_data, "testuser", "account_stars_by_day", timestamp_key="star_date")

    assert "wdm0006/my_repo: 9 total stars" in capturing.messages
    assert "wdm0006/my/repo: 9 total stars" not in capturing.messages


def test_trend_summary_csv_columns_unchanged(tmp_path, monkeypatch, underscore_repo_trend_data):
    """The saved CSV keeps its original column names and values."""
    monkeypatch.chdir(tmp_path)

    summarize_and_save(underscore_repo_trend_data, "testuser", "account_stars_by_day", timestamp_key="star_date")

    data = read_csv_output(tmp_path / "testuser_account_stars_by_day.csv")
    assert list(data[0].keys()) == [
        "star_date",
        "total_new_stars_on_day",
        "total_cumulative_stars_up_to_day",
        "wdm0006_my_repo_new_stars",
        "wdm0006_my_repo_cumulative_stars",
    ]
    assert [row["total_cumulative_stars_up_to_day"] for row in data] == ["9", "5", "2"]


def test_repos_command(runner, httpx_mock_non_strict_assertion, tmp_path, monkeypatch, sample_stargazer_data):
    httpx_mock = httpx_mock_non_strict_assertion  # Use the yielded mock
    monkeypatch.chdir(tmp_path)
    repo_name = "testowner/testrepo"

    relevant_stargazers_api_data = [
        # Data for mock_stargazers_api should match its expected input structure
        {"login": sample_stargazer_data[0]["login"], "starred_at": sample_stargazer_data[0]["starred_at"]},
        {"login": sample_stargazer_data[1]["login"], "starred_at": sample_stargazer_data[1]["starred_at"]},
    ]
    mock_stargazers_api(httpx_mock, repo_name, relevant_stargazers_api_data)

    user1_api_details = {k: v for k, v in sample_stargazer_data[0].items() if k not in ["repo", "starred_at"]}

    # Ensure all required fields by fetch_user_metadata are present, even if None
    user1_api_details.setdefault("name", None)
    user1_api_details.setdefault("company", None)
    user1_api_details.setdefault("location", None)
    user1_api_details.setdefault("email", None)
    user1_api_details.setdefault("bio", None)
    user1_api_details.setdefault("followers", 0)
    user1_api_details.setdefault("public_repos", 0)

    user2_api_details = {k: v for k, v in sample_stargazer_data[1].items() if k not in ["repo", "starred_at"]}
    user2_api_details.setdefault("name", None)
    user2_api_details.setdefault("company", None)
    user2_api_details.setdefault("location", None)
    user2_api_details.setdefault("email", None)
    user2_api_details.setdefault("bio", None)
    user2_api_details.setdefault("followers", 0)
    user2_api_details.setdefault("public_repos", 0)

    httpx_mock.add_response(
        url=f"{BASE_API_URL}/users/{sample_stargazer_data[0]['login']}",
        method="GET",
        json=user1_api_details,
        status_code=200,
    )
    httpx_mock.add_response(
        url=f"{BASE_API_URL}/users/{sample_stargazer_data[1]['login']}",
        method="GET",
        json=user2_api_details,
        status_code=200,
    )

    result = runner.invoke(cli, ["repos", repo_name], catch_exceptions=False)
    assert result.exit_code == 0, f"CLI Error: {result.output}"
    output_file = tmp_path / f"{repo_name.replace('/', '_')}_stargazers.csv"
    assert output_file.exists()
    data = read_csv_output(output_file)
    assert len(data) == 2
    assert data[0]["login"] == sample_stargazer_data[1]["login"]
    assert data[1]["login"] == sample_stargazer_data[0]["login"]


def test_repos_command_missing_repo_still_exits(runner, httpx_mock_non_strict_assertion, monkeypatch):
    httpx_mock = httpx_mock_non_strict_assertion
    capturing = CapturingConsole()
    monkeypatch.setattr("stargazers.cli.console", capturing)
    repo_name = "testowner/does-not-exist"
    httpx_mock.add_response(
        url=f"{BASE_API_URL}/repos/{repo_name}/stargazers?per_page={PER_PAGE}&page=1",
        method="GET",
        status_code=404,
        json={"message": "Not Found"},
    )

    result = runner.invoke(cli, ["repos", repo_name])

    assert result.exit_code == 1
    assert any(
        f"fetching stargazers for repo {repo_name} not found. Please check the input and try again." in message
        for message in capturing.messages
    )


def test_repos_command_skips_stargazer_with_missing_profile(
    runner, httpx_mock_non_strict_assertion, tmp_path, monkeypatch
):
    """One deleted stargazer account no longer aborts the run — the rest still reach the CSV."""
    httpx_mock = httpx_mock_non_strict_assertion
    monkeypatch.chdir(tmp_path)
    repo_name = "testowner/testrepo"

    mock_stargazers_api(
        httpx_mock,
        repo_name,
        [
            {"login": "ghostuser", "starred_at": "2023-01-01T00:00:00Z"},
            {"login": "gooduser", "starred_at": "2023-01-02T00:00:00Z"},
        ],
    )
    httpx_mock.add_response(
        url=f"{BASE_API_URL}/users/ghostuser",
        method="GET",
        status_code=404,
        text="Not Found",
    )
    httpx_mock.add_response(
        url=f"{BASE_API_URL}/users/gooduser",
        method="GET",
        json=GOOD_USER_PROFILE,
        status_code=200,
    )

    result = runner.invoke(cli, ["repos", repo_name], catch_exceptions=False)

    assert result.exit_code == 0, f"CLI Error: {result.output}"
    output_file = tmp_path / f"{repo_name.replace('/', '_')}_stargazers.csv"
    assert output_file.exists()
    data = read_csv_output(output_file)
    assert [row["login"] for row in data] == ["gooduser"]
    assert data[0]["location"] == "Reykjavik"
    assert data[0]["followers"] == "42"
    assert data[0]["repo"] == repo_name


def test_forkers_command(runner, httpx_mock_non_strict_assertion, tmp_path, monkeypatch):
    httpx_mock = httpx_mock_non_strict_assertion  # Use the yielded mock
    monkeypatch.chdir(tmp_path)
    repo_name = "testowner/testforkrepo"

    forker_api_events = [
        {"login": "forkerA", "created_at": "2023-04-01T00:00:00Z"},  # Use created_at as per API
        {"login": "forkerB", "created_at": "2023-04-02T00:00:00Z"},
    ]
    mock_forkers_api(httpx_mock, repo_name, forker_api_events)

    forkerA_metadata = {
        "login": "forkerA",
        "name": "Forker A",
        "location": "Venus",
        "company": None,
        "email": None,
        "bio": None,
        "followers": 0,
        "public_repos": 0,
    }
    forkerB_metadata = {
        "login": "forkerB",
        "name": "Forker B",
        "location": "Jupiter",
        "company": None,
        "email": None,
        "bio": None,
        "followers": 0,
        "public_repos": 0,
    }

    httpx_mock.add_response(url=f"{BASE_API_URL}/users/forkerA", method="GET", json=forkerA_metadata, status_code=200)
    httpx_mock.add_response(url=f"{BASE_API_URL}/users/forkerB", method="GET", json=forkerB_metadata, status_code=200)

    result = runner.invoke(cli, ["forkers", repo_name], catch_exceptions=False)
    assert result.exit_code == 0, f"CLI Error: {result.output}"
    output_file = tmp_path / f"{repo_name.replace('/', '_')}_forkers.csv"
    assert output_file.exists()
    data = read_csv_output(output_file)
    assert len(data) == 2
    assert data[0]["login"] == "forkerB"
    assert data[1]["login"] == "forkerA"


def test_contributors_command_paginates_enriches_and_sorts(
    runner, httpx_mock_non_strict_assertion, tmp_path, monkeypatch
):
    httpx_mock = httpx_mock_non_strict_assertion
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("stargazers.cli.time.sleep", lambda _seconds: None)
    repo = "testowner/testrepo"
    base_url = f"{BASE_API_URL}/repos/{repo}/contributors"
    httpx_mock.add_response(
        url=f"{base_url}?per_page={PER_PAGE}&page=1",
        method="GET",
        json=[{"login": "alice", "contributions": 3}],
        headers={"Link": f'<{base_url}?per_page={PER_PAGE}&page=2>; rel="next"'},
    )
    httpx_mock.add_response(
        url=f"{base_url}?per_page={PER_PAGE}&page=2",
        method="GET",
        json=[{"login": "bob", "contributions": 12}],
    )
    profiles = {
        "alice": {
            "login": "alice",
            "name": "Alice A",
            "company": "Acme",
            "location": "Earth",
            "email": "alice@example.com",
            "bio": "Builder",
            "followers": 4,
            "public_repos": 7,
        },
        "bob": {
            "login": "bob",
            "name": "Bob B",
            "company": None,
            "location": "Mars",
            "email": None,
            "bio": "Maintainer",
            "followers": 9,
            "public_repos": 2,
        },
    }
    for login, profile in profiles.items():
        httpx_mock.add_response(url=f"{BASE_API_URL}/users/{login}", method="GET", json=profile)

    result = runner.invoke(cli, ["contributors", repo], catch_exceptions=False)

    assert result.exit_code == 0
    output_file = tmp_path / "testowner_testrepo_contributors.csv"
    assert output_file.exists()
    with open(output_file, encoding="utf-8") as csv_file:
        reader = csv.DictReader(csv_file)
        assert reader.fieldnames == [
            "login",
            "name",
            "company",
            "location",
            "email",
            "bio",
            "followers",
            "public_repos",
            "contributions",
            "repo",
        ]
        assert list(reader) == [
            {
                "login": "bob",
                "name": "Bob B",
                "company": "",
                "location": "Mars",
                "email": "",
                "bio": "Maintainer",
                "followers": "9",
                "public_repos": "2",
                "contributions": "12",
                "repo": repo,
            },
            {
                "login": "alice",
                "name": "Alice A",
                "company": "Acme",
                "location": "Earth",
                "email": "alice@example.com",
                "bio": "Builder",
                "followers": "4",
                "public_repos": "7",
                "contributions": "3",
                "repo": repo,
            },
        ]
    contributor_requests = [request for request in httpx_mock.get_requests() if "/contributors" in str(request.url)]
    assert [request.url.params["page"] for request in contributor_requests] == ["1", "2"]


def test_contributors_command_empty_repo_writes_no_csv(runner, httpx_mock_non_strict_assertion, tmp_path, monkeypatch):
    httpx_mock = httpx_mock_non_strict_assertion
    capturing = CapturingConsole()
    monkeypatch.setattr("stargazers.cli.console", capturing)
    monkeypatch.chdir(tmp_path)
    repo = "testowner/empty"
    httpx_mock.add_response(
        url=f"{BASE_API_URL}/repos/{repo}/contributors?per_page={PER_PAGE}&page=1",
        method="GET",
        status_code=204,
    )

    result = runner.invoke(cli, ["contributors", repo], catch_exceptions=False)

    assert result.exit_code == 0
    assert not list(tmp_path.glob("*.csv"))
    assert any("No contributors found for any repository." in message for message in capturing.messages)


def test_fetch_contributors_rate_limit_is_bounded(httpx_mock_non_strict_assertion, monkeypatch):
    httpx_mock = httpx_mock_non_strict_assertion
    capturing = CapturingConsole()
    monkeypatch.setattr("stargazers.cli.console", capturing)
    monkeypatch.setattr("stargazers.cli.time.sleep", lambda _seconds: None)
    repo = "testowner/testrepo"
    base_url = f"{BASE_API_URL}/repos/{repo}/contributors"
    httpx_mock.add_response(
        url=f"{base_url}?per_page={PER_PAGE}&page=1",
        method="GET",
        json=[{"login": "alice", "contributions": 3}],
        headers={"Link": f'<{base_url}?per_page={PER_PAGE}&page=2>; rel="next"'},
    )
    httpx_mock.add_response(
        url=f"{base_url}?per_page={PER_PAGE}&page=2",
        method="GET",
        status_code=403,
        text="API rate limit exceeded",
        is_reusable=True,
    )

    contributors, complete = fetch_contributors(repo)

    assert contributors == [
        {"login": "alice", "contributions": 3, "user_details": {"login": "alice", "contributions": 3}}
    ]
    assert complete is False
    page_two_requests = [request for request in httpx_mock.get_requests() if request.url.params["page"] == "2"]
    assert len(page_two_requests) == MAX_RATE_LIMIT_RETRIES + 1
    assert any(f"WARNING: incomplete data for {repo}" in message for message in capturing.messages)


def test_contributors_multiple_repos_and_invalid_argument(
    runner, httpx_mock_non_strict_assertion, tmp_path, monkeypatch
):
    httpx_mock = httpx_mock_non_strict_assertion
    capturing = CapturingConsole()
    monkeypatch.setattr("stargazers.cli.console", capturing)
    monkeypatch.setattr("stargazers.cli.time.sleep", lambda _seconds: None)
    monkeypatch.chdir(tmp_path)
    repos = ("owner/one", "owner/two")
    for index, repo in enumerate(repos, start=1):
        login = f"user{index}"
        httpx_mock.add_response(
            url=f"{BASE_API_URL}/repos/{repo}/contributors?per_page={PER_PAGE}&page=1",
            method="GET",
            json=[{"login": login, "contributions": index}],
        )
        httpx_mock.add_response(
            url=f"{BASE_API_URL}/users/{login}",
            method="GET",
            json={
                "login": login,
                "name": login,
                "company": None,
                "location": None,
                "email": None,
                "bio": None,
                "followers": 0,
                "public_repos": 1,
            },
        )

    result = runner.invoke(cli, ["contributors", "invalid", *repos], catch_exceptions=False)

    assert result.exit_code == 0
    data = read_csv_output(tmp_path / "all_repos_contributors.csv")
    assert [(row["login"], row["contributions"], row["repo"]) for row in data] == [
        ("user2", "2", "owner/two"),
        ("user1", "1", "owner/one"),
    ]
    assert any(
        "Invalid repository format: 'invalid'. Must be 'owner/repo'." in message for message in capturing.messages
    )


ISSUES_BASE_PARAMS = f"state=all&per_page={PER_PAGE}"


def _issue_payload(
    number,
    *,
    created_at,
    is_pr=False,
    state="closed",
    closed_at=None,
    author="alice",
    comments=0,
    labels=(),
):
    """Build a single item exactly as GitHub's /issues list endpoint returns it."""
    item = {
        "number": number,
        "title": f"Item {number}",
        "state": state,
        "comments": comments,
        "created_at": created_at,
        "closed_at": closed_at,
        "user": {"login": author},
        "labels": [{"name": name} for name in labels],
    }
    if is_pr:
        # Present ONLY on pull requests — this key is what distinguishes them.
        item["pull_request"] = {"url": f"https://api.github.com/repos/x/y/pulls/{number}"}
    return item


def test_issues_command_types_pagination_and_time_to_close(
    runner, httpx_mock_non_strict_assertion, tmp_path, monkeypatch
):
    """Two pages of mixed issues/PRs land in one CSV with exact types and days_to_close."""
    httpx_mock = httpx_mock_non_strict_assertion
    capturing = CapturingConsole()
    monkeypatch.setattr("stargazers.cli.console", capturing)
    monkeypatch.setattr("stargazers.cli.time.sleep", lambda _seconds: None)
    monkeypatch.setattr("stargazers.cli._utcnow", lambda: datetime(2024, 1, 21, tzinfo=timezone.utc))
    monkeypatch.chdir(tmp_path)
    repo = "testowner/testrepo"
    base_url = f"{BASE_API_URL}/repos/{repo}/issues"

    httpx_mock.add_response(
        url=f"{base_url}?{ISSUES_BASE_PARAMS}&page=1",
        method="GET",
        json=[
            _issue_payload(
                4,
                created_at="2024-01-01T00:00:00Z",
                state="open",
                comments=5,
                labels=("bug", "help wanted"),
            ),
            _issue_payload(
                3,
                created_at="2024-01-02T00:00:00Z",
                closed_at="2024-01-03T00:00:00Z",
                is_pr=True,
                author="bob",
                comments=2,
            ),
        ],
        headers={"Link": f'<{base_url}?{ISSUES_BASE_PARAMS}&page=2>; rel="next"'},
    )
    httpx_mock.add_response(
        url=f"{base_url}?{ISSUES_BASE_PARAMS}&page=2",
        method="GET",
        json=[
            _issue_payload(2, created_at="2024-01-05T00:00:00Z", closed_at="2024-01-07T12:00:00Z"),
            _issue_payload(1, created_at="2024-01-10T00:00:00Z", closed_at="2024-01-16T00:00:00Z", is_pr=True),
        ],
    )

    result = runner.invoke(cli, ["issues", repo], catch_exceptions=False)

    assert result.exit_code == 0
    output_file = tmp_path / "testowner_testrepo_issues.csv"
    assert output_file.exists()
    with open(output_file, encoding="utf-8") as csv_file:
        reader = csv.DictReader(csv_file)
        assert reader.fieldnames == [
            "number",
            "type",
            "title",
            "author",
            "state",
            "labels",
            "comments",
            "created_at",
            "closed_at",
            "days_to_close",
            "repo",
        ]
        rows = list(reader)

    # Newest-first by created_at, with per-row type driven by the `pull_request` key.
    assert [(row["number"], row["type"]) for row in rows] == [("1", "pr"), ("2", "issue"), ("3", "pr"), ("4", "issue")]
    # Exact durations: 6 days, 2.5 days, 1 day, and blank for the still-open item.
    assert [row["days_to_close"] for row in rows] == ["6.0", "2.5", "1.0", ""]
    assert [row["created_at"] for row in rows] == [
        "2024-01-10T00:00:00Z",
        "2024-01-05T00:00:00Z",
        "2024-01-02T00:00:00Z",
        "2024-01-01T00:00:00Z",
    ]
    assert [row["closed_at"] for row in rows] == [
        "2024-01-16T00:00:00Z",
        "2024-01-07T12:00:00Z",
        "2024-01-03T00:00:00Z",
        "",
    ]
    assert [row["author"] for row in rows] == ["alice", "alice", "bob", "alice"]
    assert [row["comments"] for row in rows] == ["0", "0", "2", "5"]
    assert [row["labels"] for row in rows] == ["", "", "", "bug, help wanted"]
    assert {row["repo"] for row in rows} == {repo}

    # Both pages were requested, in order.
    issue_requests = [request for request in httpx_mock.get_requests() if "/issues" in str(request.url)]
    assert [request.url.params["page"] for request in issue_requests] == ["1", "2"]
    assert [request.url.params["state"] for request in issue_requests] == ["all", "all"]

    # Summary values: median of [1.0, 2.5, 6.0] is 2.5; p90 (linear) is 5.3.
    assert "Issues: 1 open, 1 closed" in capturing.messages
    assert "Pull requests: 0 open, 2 closed" in capturing.messages
    assert "Median days to close: 2.5" in capturing.messages
    assert "P90 days to close: 5.3" in capturing.messages
    assert f"Oldest open item: #4 in {repo}, opened 20.0 days ago" in capturing.messages
    assert "alice: 3 items" in capturing.messages
    assert "bob: 1 items" in capturing.messages


def test_issues_command_multiple_repos_and_invalid_argument(
    runner, httpx_mock_non_strict_assertion, tmp_path, monkeypatch
):
    httpx_mock = httpx_mock_non_strict_assertion
    capturing = CapturingConsole()
    monkeypatch.setattr("stargazers.cli.console", capturing)
    monkeypatch.chdir(tmp_path)
    repos = ("owner/one", "owner/two")
    for index, repo in enumerate(repos, start=1):
        httpx_mock.add_response(
            url=f"{BASE_API_URL}/repos/{repo}/issues?{ISSUES_BASE_PARAMS}&page=1",
            method="GET",
            json=[
                _issue_payload(
                    index,
                    created_at=f"2024-02-0{index}T00:00:00Z",
                    closed_at=f"2024-02-0{index + 1}T00:00:00Z",
                    author=f"user{index}",
                )
            ],
        )

    result = runner.invoke(cli, ["issues", "invalid", *repos], catch_exceptions=False)

    assert result.exit_code == 0
    assert not (tmp_path / "owner_one_issues.csv").exists()
    rows = read_csv_output(tmp_path / "all_repos_issues.csv")
    assert [(row["number"], row["repo"], row["days_to_close"]) for row in rows] == [
        ("2", "owner/two", "1.0"),
        ("1", "owner/one", "1.0"),
    ]
    assert any(
        "Invalid repository format: 'invalid'. Must be 'owner/repo'." in message for message in capturing.messages
    )


def test_issues_command_skips_repo_with_issues_disabled(runner, httpx_mock_non_strict_assertion, tmp_path, monkeypatch):
    """A 410 Gone for one repository is skipped, and the other repository still lands in the CSV."""
    httpx_mock = httpx_mock_non_strict_assertion
    capturing = CapturingConsole()
    monkeypatch.setattr("stargazers.cli.console", capturing)
    monkeypatch.chdir(tmp_path)
    httpx_mock.add_response(
        url=f"{BASE_API_URL}/repos/owner/disabled/issues?{ISSUES_BASE_PARAMS}&page=1",
        method="GET",
        status_code=410,
        text="Issues are disabled for this repo",
    )
    httpx_mock.add_response(
        url=f"{BASE_API_URL}/repos/owner/enabled/issues?{ISSUES_BASE_PARAMS}&page=1",
        method="GET",
        json=[_issue_payload(7, created_at="2024-03-01T00:00:00Z", state="open")],
    )

    result = runner.invoke(cli, ["issues", "owner/disabled", "owner/enabled"], catch_exceptions=False)

    assert result.exit_code == 0
    assert any("Issues are disabled for owner/disabled" in message for message in capturing.messages)
    rows = read_csv_output(tmp_path / "all_repos_issues.csv")
    assert [(row["number"], row["repo"]) for row in rows] == [("7", "owner/enabled")]


def test_fetch_issues_rate_limit_is_bounded(httpx_mock_non_strict_assertion, monkeypatch, no_sleep):
    """A persistent rate-limit 403 returns page-1 data flagged incomplete after the retry cap."""
    httpx_mock = httpx_mock_non_strict_assertion
    capturing = CapturingConsole()
    monkeypatch.setattr("stargazers.cli.console", capturing)
    repo = "testowner/testrepo"
    base_url = f"{BASE_API_URL}/repos/{repo}/issues"
    httpx_mock.add_response(
        url=f"{base_url}?{ISSUES_BASE_PARAMS}&page=1",
        method="GET",
        json=[_issue_payload(1, created_at="2024-01-01T00:00:00Z", state="open")],
        headers={"Link": f'<{base_url}?{ISSUES_BASE_PARAMS}&page=2>; rel="next"'},
    )
    httpx_mock.add_response(
        url=f"{base_url}?{ISSUES_BASE_PARAMS}&page=2",
        method="GET",
        status_code=403,
        text="API rate limit exceeded",
        is_reusable=True,
    )

    items, complete = fetch_issues(repo)

    assert complete is False
    assert items == [
        {
            "number": 1,
            "type": "issue",
            "title": "Item 1",
            "author": "alice",
            "state": "open",
            "labels": "",
            "comments": 0,
            "created_at": "2024-01-01T00:00:00Z",
            "closed_at": None,
        }
    ]
    page_two_requests = [request for request in httpx_mock.get_requests() if request.url.params["page"] == "2"]
    assert len(page_two_requests) == MAX_RATE_LIMIT_RETRIES + 1
    assert any(f"WARNING: incomplete data for {repo}" in message for message in capturing.messages)


RELEASES_BASE_PARAMS = f"per_page={PER_PAGE}"


def _release_payload(tag, *, published_at, downloads=(), author="alice", draft=False, prerelease=False):
    """Build a single release exactly as GitHub's /releases list endpoint returns it."""
    return {
        "tag_name": tag,
        "name": f"Release {tag}",
        "draft": draft,
        "prerelease": prerelease,
        "created_at": published_at,
        "published_at": published_at,
        "author": {"login": author},
        "assets": [
            {"name": f"{tag}-asset-{index}.whl", "size": 100, "download_count": count}
            for index, count in enumerate(downloads)
        ],
    }


def test_releases_command_downloads_assets_and_cadence(runner, httpx_mock_non_strict_assertion, tmp_path, monkeypatch):
    """Two pages of releases land in one CSV with exact download sums, asset counts and gaps."""
    httpx_mock = httpx_mock_non_strict_assertion
    capturing = CapturingConsole()
    monkeypatch.setattr("stargazers.cli.console", capturing)
    monkeypatch.setattr("stargazers.cli.time.sleep", lambda _seconds: None)
    monkeypatch.chdir(tmp_path)
    repo = "testowner/testrepo"
    base_url = f"{BASE_API_URL}/repos/{repo}/releases"

    # GitHub returns releases newest first.
    httpx_mock.add_response(
        url=f"{base_url}?{RELEASES_BASE_PARAMS}&page=1",
        method="GET",
        json=[
            _release_payload("v4", published_at="2024-03-11T00:00:00Z", downloads=(5, 7)),
            _release_payload("v3", published_at="2024-03-01T00:00:00Z", downloads=(100,), author="bob"),
        ],
        headers={"Link": f'<{base_url}?{RELEASES_BASE_PARAMS}&page=2>; rel="next"'},
    )
    httpx_mock.add_response(
        url=f"{base_url}?{RELEASES_BASE_PARAMS}&page=2",
        method="GET",
        json=[
            _release_payload("v2", published_at="2024-02-10T12:00:00Z", downloads=(1, 2, 3), prerelease=True),
            _release_payload("v1", published_at="2024-01-31T12:00:00Z"),
        ],
    )

    result = runner.invoke(cli, ["releases", repo], catch_exceptions=False)

    assert result.exit_code == 0
    output_file = tmp_path / "testowner_testrepo_releases.csv"
    assert output_file.exists()
    with open(output_file, encoding="utf-8") as csv_file:
        reader = csv.DictReader(csv_file)
        assert reader.fieldnames == [
            "tag_name",
            "name",
            "author",
            "draft",
            "prerelease",
            "created_at",
            "published_at",
            "days_since_previous",
            "assets",
            "downloads",
            "repo",
        ]
        rows = list(reader)

    assert [row["tag_name"] for row in rows] == ["v4", "v3", "v2", "v1"]
    # downloads is the SUM across a release's assets; assets is the count.
    assert [row["downloads"] for row in rows] == ["12", "100", "6", "0"]
    assert [row["assets"] for row in rows] == ["2", "1", "3", "0"]
    # Hand-computed gaps: 2024-03-01 -> 03-11 is 10 days; 02-10T12:00 -> 03-01T00:00 is
    # 19.5 (2024 is a leap year); 01-31T12:00 -> 02-10T12:00 is 10. The oldest has none.
    assert [row["days_since_previous"] for row in rows] == ["10.0", "19.5", "10.0", ""]
    assert [row["published_at"] for row in rows] == [
        "2024-03-11T00:00:00Z",
        "2024-03-01T00:00:00Z",
        "2024-02-10T12:00:00Z",
        "2024-01-31T12:00:00Z",
    ]
    assert [row["author"] for row in rows] == ["alice", "bob", "alice", "alice"]
    assert [row["prerelease"] for row in rows] == ["False", "False", "True", "False"]
    assert {row["repo"] for row in rows} == {repo}
    assert {row["name"] for row in rows} == {"Release v1", "Release v2", "Release v3", "Release v4"}

    release_requests = [request for request in httpx_mock.get_requests() if "/releases" in str(request.url)]
    assert [request.url.params["page"] for request in release_requests] == ["1", "2"]

    assert "Total releases: 4" in capturing.messages
    assert "Total downloads: 118" in capturing.messages
    # Median of [10.0, 19.5, 10.0].
    assert "Median days between releases: 10.0" in capturing.messages
    assert "Latest release: 2024-03-11T00:00:00Z" in capturing.messages
    assert f"{repo} v3: 100 downloads" in capturing.messages
    assert f"{repo} v4: 12 downloads" in capturing.messages


def test_releases_command_cadence_is_per_repository(runner, httpx_mock_non_strict_assertion, tmp_path, monkeypatch):
    """Two repositories whose releases interleave in time keep separate cadences.

    A globally-sorted list would yield 5.0/5.0/10.0 with a single blank; per repository the
    gaps are 10.0 and 15.0 with a blank for each repository's own oldest release.
    """
    httpx_mock = httpx_mock_non_strict_assertion
    capturing = CapturingConsole()
    monkeypatch.setattr("stargazers.cli.console", capturing)
    monkeypatch.chdir(tmp_path)
    httpx_mock.add_response(
        url=f"{BASE_API_URL}/repos/owner/one/releases?{RELEASES_BASE_PARAMS}&page=1",
        method="GET",
        json=[
            _release_payload("one-b", published_at="2024-01-11T00:00:00Z", downloads=(4,)),
            _release_payload("one-a", published_at="2024-01-01T00:00:00Z"),
        ],
    )
    httpx_mock.add_response(
        url=f"{BASE_API_URL}/repos/owner/two/releases?{RELEASES_BASE_PARAMS}&page=1",
        method="GET",
        json=[
            _release_payload("two-b", published_at="2024-01-21T00:00:00Z"),
            _release_payload("two-a", published_at="2024-01-06T00:00:00Z"),
        ],
    )

    result = runner.invoke(cli, ["releases", "invalid", "owner/one", "owner/two"], catch_exceptions=False)

    assert result.exit_code == 0
    assert not (tmp_path / "owner_one_releases.csv").exists()
    rows = read_csv_output(tmp_path / "all_repos_releases.csv")
    assert [(row["tag_name"], row["repo"], row["days_since_previous"]) for row in rows] == [
        ("two-b", "owner/two", "15.0"),
        ("one-b", "owner/one", "10.0"),
        ("two-a", "owner/two", ""),
        ("one-a", "owner/one", ""),
    ]
    # An argument without a slash is skipped and the run continues.
    assert any(
        "Invalid repository format: 'invalid'. Must be 'owner/repo'." in message for message in capturing.messages
    )


def test_releases_command_repo_with_no_releases_writes_nothing(
    runner, httpx_mock_non_strict_assertion, tmp_path, monkeypatch
):
    """GitHub answers 200 with an empty array — that must log a message, not error."""
    httpx_mock = httpx_mock_non_strict_assertion
    capturing = CapturingConsole()
    monkeypatch.setattr("stargazers.cli.console", capturing)
    monkeypatch.chdir(tmp_path)
    httpx_mock.add_response(
        url=f"{BASE_API_URL}/repos/owner/quiet/releases?{RELEASES_BASE_PARAMS}&page=1",
        method="GET",
        json=[],
    )

    result = runner.invoke(cli, ["releases", "owner/quiet"], catch_exceptions=False)

    assert result.exit_code == 0
    assert list(tmp_path.glob("*.csv")) == []
    assert any("No releases found for any repository." in message for message in capturing.messages)


def test_fetch_releases_rate_limit_is_bounded(httpx_mock_non_strict_assertion, monkeypatch, no_sleep):
    """A persistent rate-limit 403 returns page-1 data flagged incomplete after the retry cap."""
    httpx_mock = httpx_mock_non_strict_assertion
    capturing = CapturingConsole()
    monkeypatch.setattr("stargazers.cli.console", capturing)
    repo = "testowner/testrepo"
    base_url = f"{BASE_API_URL}/repos/{repo}/releases"
    httpx_mock.add_response(
        url=f"{base_url}?{RELEASES_BASE_PARAMS}&page=1",
        method="GET",
        json=[_release_payload("v1", published_at="2024-01-01T00:00:00Z", downloads=(3, 4))],
        headers={"Link": f'<{base_url}?{RELEASES_BASE_PARAMS}&page=2>; rel="next"'},
    )
    httpx_mock.add_response(
        url=f"{base_url}?{RELEASES_BASE_PARAMS}&page=2",
        method="GET",
        status_code=403,
        text="API rate limit exceeded",
        is_reusable=True,
    )

    items, complete = fetch_releases(repo)

    assert complete is False
    assert items == [
        {
            "tag_name": "v1",
            "name": "Release v1",
            "author": "alice",
            "draft": False,
            "prerelease": False,
            "created_at": "2024-01-01T00:00:00Z",
            "published_at": "2024-01-01T00:00:00Z",
            "days_since_previous": None,
            "assets": 2,
            "downloads": 7,
        }
    ]
    page_two_requests = [request for request in httpx_mock.get_requests() if request.url.params["page"] == "2"]
    assert len(page_two_requests) == MAX_RATE_LIMIT_RETRIES + 1
    assert any(f"WARNING: incomplete data for {repo}" in message for message in capturing.messages)


def test_cli_help_lists_releases_command(runner):
    result = runner.invoke(cli, ["--help"], catch_exceptions=False)

    assert result.exit_code == 0
    assert "releases" in result.output


COMMITS_BASE_PARAMS = f"per_page={PER_PAGE}"


def _commit_payload(sha, *, name, authored_at, login="alice", parents=1, committed_at=None, message=None):
    return {
        "sha": sha,
        "author": {"login": login} if login else None,
        "commit": {
            "author": {"name": name, "date": authored_at},
            "committer": {"name": "Committer", "date": committed_at or authored_at},
            "message": message or f"Commit {sha}",
        },
        "parents": [{"sha": f"parent-{index}"} for index in range(parents)],
    }


def test_fetch_commits_flattens_linked_unlinked_and_merge_authors(httpx_mock_non_strict_assertion):
    repo = "owner/repo"
    httpx_mock_non_strict_assertion.add_response(
        url=f"{BASE_API_URL}/repos/{repo}/commits?{COMMITS_BASE_PARAMS}&page=1",
        method="GET",
        json=[
            _commit_payload("linked", name="Alice A", authored_at="2024-02-03T04:05:06Z"),
            _commit_payload("unlinked", name="Git Author", authored_at="2024-02-02T03:04:05Z", login=None),
            _commit_payload("merge", name="Merger", authored_at="2024-02-01T02:03:04Z", parents=2),
        ],
    )

    items, complete = fetch_commits(repo)

    assert complete is True
    assert items == [
        {
            "sha": "linked",
            "author_login": "alice",
            "author_name": "Alice A",
            "authored_at": "2024-02-03T04:05:06Z",
            "committed_at": "2024-02-03T04:05:06Z",
            "message": "Commit linked",
            "is_merge": False,
        },
        {
            "sha": "unlinked",
            "author_login": None,
            "author_name": "Git Author",
            "authored_at": "2024-02-02T03:04:05Z",
            "committed_at": "2024-02-02T03:04:05Z",
            "message": "Commit unlinked",
            "is_merge": False,
        },
        {
            "sha": "merge",
            "author_login": "alice",
            "author_name": "Merger",
            "authored_at": "2024-02-01T02:03:04Z",
            "committed_at": "2024-02-01T02:03:04Z",
            "message": "Commit merge",
            "is_merge": True,
        },
    ]
    assert [request.url.path for request in httpx_mock_non_strict_assertion.get_requests()] == [
        "/repos/owner/repo/commits"
    ]


def test_fetch_commits_page_two_network_failure_returns_partial_data(
    httpx_mock_non_strict_assertion, monkeypatch, no_sleep
):
    capturing = CapturingConsole()
    monkeypatch.setattr("stargazers.cli.console", capturing)
    repo = "owner/repo"
    base_url = f"{BASE_API_URL}/repos/{repo}/commits"
    httpx_mock_non_strict_assertion.add_response(
        url=f"{base_url}?{COMMITS_BASE_PARAMS}&page=1",
        method="GET",
        json=[_commit_payload("one", name="One", authored_at="2024-01-01T00:00:00Z")],
        headers={"Link": f'<{base_url}?{COMMITS_BASE_PARAMS}&page=2>; rel="next"'},
    )
    httpx_mock_non_strict_assertion.add_exception(
        httpx.ReadError("connection lost"), url=f"{base_url}?{COMMITS_BASE_PARAMS}&page=2"
    )

    items, complete = fetch_commits(repo)

    assert complete is False
    assert [(item["sha"], item["author_name"]) for item in items] == [("one", "One")]
    assert any(f"WARNING: incomplete data for {repo}" in message for message in capturing.messages)


def test_fetch_commits_paginates_with_exact_values(httpx_mock_non_strict_assertion, no_sleep):
    repo = "owner/repo"
    base_url = f"{BASE_API_URL}/repos/{repo}/commits"
    httpx_mock_non_strict_assertion.add_response(
        url=f"{base_url}?{COMMITS_BASE_PARAMS}&page=1",
        method="GET",
        json=[_commit_payload("new", name="New Author", authored_at="2024-02-02T00:00:00Z")],
        headers={"Link": f'<{base_url}?{COMMITS_BASE_PARAMS}&page=2>; rel="next"'},
    )
    httpx_mock_non_strict_assertion.add_response(
        url=f"{base_url}?{COMMITS_BASE_PARAMS}&page=2",
        method="GET",
        json=[
            _commit_payload(
                "old",
                name="Old Author",
                authored_at="2024-02-01T00:00:00Z",
                committed_at="2024-02-01T01:00:00Z",
                login=None,
                parents=2,
                message="Merge old work",
            )
        ],
    )

    items, complete = fetch_commits(repo)

    assert complete is True
    assert [(item["sha"], item["author_name"], item["author_login"], item["is_merge"]) for item in items] == [
        ("new", "New Author", "alice", False),
        ("old", "Old Author", None, True),
    ]
    assert items[1]["committed_at"] == "2024-02-01T01:00:00Z"
    assert items[1]["message"] == "Merge old work"


def test_fetch_commits_rate_limit_is_bounded(httpx_mock_non_strict_assertion, monkeypatch, no_sleep):
    capturing = CapturingConsole()
    monkeypatch.setattr("stargazers.cli.console", capturing)
    repo = "owner/repo"
    url = f"{BASE_API_URL}/repos/{repo}/commits?{COMMITS_BASE_PARAMS}&page=1"
    httpx_mock_non_strict_assertion.add_response(
        url=url, method="GET", status_code=403, text="API rate limit exceeded", is_reusable=True
    )

    items, complete = fetch_commits(repo)

    assert items == []
    assert complete is False
    assert len(httpx_mock_non_strict_assertion.get_requests()) == MAX_RATE_LIMIT_RETRIES + 1
    assert any(f"WARNING: incomplete data for {repo}" in message for message in capturing.messages)


def test_commits_command_multi_repo_csv_order_and_summary(
    runner, httpx_mock_non_strict_assertion, tmp_path, monkeypatch
):
    capturing = CapturingConsole()
    monkeypatch.setattr("stargazers.cli.console", capturing)
    monkeypatch.chdir(tmp_path)
    fixtures = {
        "owner/one": [
            _commit_payload("a3", name="Alice", authored_at="2024-01-03T12:00:00Z", parents=2),
            _commit_payload("a1", name="Alice", authored_at="2024-01-01T12:00:00Z"),
        ],
        "owner/two": [
            _commit_payload("b3", name="Bob", authored_at="2024-01-03T18:00:00Z", login=None),
            _commit_payload("b2", name="Bob", authored_at="2024-01-02T12:00:00Z", login=None),
            _commit_payload("b1", name="Alice", authored_at="2024-01-01T18:00:00Z"),
        ],
    }
    for repo, payload in fixtures.items():
        httpx_mock_non_strict_assertion.add_response(
            url=f"{BASE_API_URL}/repos/{repo}/commits?{COMMITS_BASE_PARAMS}&page=1", method="GET", json=payload
        )

    result = runner.invoke(cli, ["commits", "owner/one", "owner/two"], catch_exceptions=False)

    assert result.exit_code == 0
    with open(tmp_path / "all_repos_commits.csv", encoding="utf-8") as csv_file:
        reader = csv.DictReader(csv_file)
        assert reader.fieldnames == [
            "sha",
            "author_login",
            "author_name",
            "authored_at",
            "committed_at",
            "message",
            "is_merge",
            "repo",
        ]
        rows = list(reader)
    assert [(row["sha"], row["repo"], row["author_login"]) for row in rows] == [
        ("b3", "owner/two", ""),
        ("a3", "owner/one", "alice"),
        ("b2", "owner/two", ""),
        ("b1", "owner/two", "alice"),
        ("a1", "owner/one", "alice"),
    ]
    assert "Total commits: 5" in capturing.messages
    assert "Merge commits: 1" in capturing.messages
    assert "Non-merge commits: 4" in capturing.messages
    assert "Active date range: 2024-01-01 to 2024-01-03" in capturing.messages
    assert "Median commits per active day: 2.0" in capturing.messages
    assert "Alice: 3 commits" in capturing.messages
    assert "Bob: 2 commits" in capturing.messages


def test_cli_help_lists_commits_command(runner):
    result = runner.invoke(cli, ["--help"], catch_exceptions=False)

    assert result.exit_code == 0
    assert "commits" in result.output


PARTIAL_COMMAND_CASES = [
    (
        "repos",
        "stargazers",
        [{"user": {"login": "partial-user"}, "starred_at": "2024-01-01T00:00:00Z"}],
    ),
    (
        "forkers",
        "forks",
        [{"owner": {"login": "partial-user"}, "created_at": "2024-01-01T00:00:00Z"}],
    ),
    ("contributors", "contributors", [{"login": "partial-user", "contributions": 3}]),
    (
        "issues",
        "issues",
        [
            {
                "number": 1,
                "title": "Partial issue",
                "user": {"login": "partial-user"},
                "state": "open",
                "labels": [],
                "comments": 0,
                "created_at": "2024-01-01T00:00:00Z",
                "closed_at": None,
            }
        ],
    ),
    (
        "releases",
        "releases",
        [
            {
                "tag_name": "v1.0.0",
                "name": "First",
                "author": {"login": "partial-user"},
                "draft": False,
                "prerelease": False,
                "created_at": "2024-01-01T00:00:00Z",
                "published_at": "2024-01-01T00:00:00Z",
                "assets": [],
            }
        ],
    ),
    (
        "commits",
        "commits",
        [
            {
                "sha": "abc123",
                "author": {"login": "partial-user"},
                "commit": {
                    "author": {"name": "Partial User", "date": "2024-01-01T00:00:00Z"},
                    "committer": {"date": "2024-01-01T00:00:00Z"},
                    "message": "First commit",
                },
                "parents": [],
            }
        ],
    ),
]


def _mock_command_page(httpx_mock, repo, endpoint, payload, *, incomplete):
    query = "state=all&per_page=100&page=1" if endpoint == "issues" else "per_page=100&page=1"
    next_query = "state=all&per_page=100&page=2" if endpoint == "issues" else "per_page=100&page=2"
    base_url = f"{BASE_API_URL}/repos/{repo}/{endpoint}"
    headers = {"Link": f'<{base_url}?{next_query}>; rel="next"'} if incomplete else {}
    httpx_mock.add_response(url=f"{base_url}?{query}", json=payload, headers=headers)
    if incomplete:
        httpx_mock.add_exception(httpx.ConnectError("connection dropped"), url=f"{base_url}?{next_query}")

    if endpoint in {"stargazers", "forks", "contributors"}:
        httpx_mock.add_response(url=f"{BASE_API_URL}/users/partial-user", json=GOOD_USER_PROFILE)


@pytest.mark.parametrize(("command", "endpoint", "payload"), PARTIAL_COMMAND_CASES)
def test_repository_command_saves_partial_rows_then_warns(
    command, endpoint, payload, runner, httpx_mock_non_strict_assertion, tmp_path, monkeypatch
):
    httpx_mock = httpx_mock_non_strict_assertion
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("stargazers.cli.time.sleep", lambda _seconds: None)
    capturing = CapturingConsole()
    monkeypatch.setattr("stargazers.cli.console", capturing)
    repo = "testowner/partial"
    _mock_command_page(httpx_mock, repo, endpoint, payload, incomplete=True)

    result = runner.invoke(cli, [command, repo], catch_exceptions=False)

    assert result.exit_code == 0
    output_file = tmp_path / f"testowner_partial_{command if command != 'repos' else 'stargazers'}.csv"
    assert output_file.exists()
    assert len(read_csv_output(output_file)) == 1
    saved_index = next(i for i, message in enumerate(capturing.messages) if "Saved 1 items" in message)
    warning_index = next(i for i, message in enumerate(capturing.messages) if "the saved file UNDERCOUNTS" in message)
    assert warning_index > saved_index
    assert repo in capturing.messages[warning_index]


@pytest.mark.parametrize(("command", "endpoint", "payload"), PARTIAL_COMMAND_CASES)
def test_repository_command_does_not_warn_for_complete_fetch(
    command, endpoint, payload, runner, httpx_mock_non_strict_assertion, tmp_path, monkeypatch
):
    httpx_mock = httpx_mock_non_strict_assertion
    monkeypatch.chdir(tmp_path)
    capturing = CapturingConsole()
    monkeypatch.setattr("stargazers.cli.console", capturing)
    repo = "testowner/complete"
    _mock_command_page(httpx_mock, repo, endpoint, payload, incomplete=False)

    result = runner.invoke(cli, [command, repo], catch_exceptions=False)

    assert result.exit_code == 0
    assert not any("the saved file UNDERCOUNTS" in message for message in capturing.messages)


def test_repository_command_warning_names_only_incomplete_repo(
    runner, httpx_mock_non_strict_assertion, tmp_path, monkeypatch
):
    httpx_mock = httpx_mock_non_strict_assertion
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("stargazers.cli.time.sleep", lambda _seconds: None)
    capturing = CapturingConsole()
    monkeypatch.setattr("stargazers.cli.console", capturing)
    payload = [{"login": "partial-user", "contributions": 3}]
    _mock_command_page(httpx_mock, "testowner/complete", "contributors", payload, incomplete=False)
    _mock_command_page(httpx_mock, "testowner/partial", "contributors", payload, incomplete=True)

    result = runner.invoke(cli, ["contributors", "testowner/complete", "testowner/partial"], catch_exceptions=False)

    assert result.exit_code == 0
    warning = next(message for message in capturing.messages if "the saved file UNDERCOUNTS" in message)
    assert "testowner/partial" in warning
    assert "testowner/complete" not in warning


@patch("stargazers.cli.plt")
def test_plot_command_account_trend(mock_plt, runner, tmp_path, monkeypatch):
    """Test plotting account trend data from a CSV file."""
    monkeypatch.chdir(tmp_path)

    # Create a test CSV file
    test_data = [
        {"star_date": "2023-01-01", "total_new_stars_on_day": 2, "total_cumulative_stars_up_to_day": 2},
        {"star_date": "2023-01-02", "total_new_stars_on_day": 1, "total_cumulative_stars_up_to_day": 3},
    ]
    test_csv = tmp_path / "testuser_account_stars_by_day.csv"
    pd.DataFrame(test_data).to_csv(test_csv, index=False)

    # Test with default title (inferred from filename)
    result = runner.invoke(cli, ["plot", "--file", str(test_csv), "--type", "account-trend"], catch_exceptions=False)
    assert result.exit_code == 0, f"CLI Error: {result.output}"

    mock_plt.clc.assert_called_once()
    mock_plt.title.assert_called_once_with("Cumulative Stars Over Time for testuser")
    mock_plt.xlabel.assert_called_once_with("Days since first star")
    mock_plt.ylabel.assert_called_once_with("Cumulative Stars")
    assert mock_plt.scatter.call_count == 1
    mock_plt.show.assert_called_once()


@patch("stargazers.cli.plt")
def test_plot_command_account_trend_custom_title(mock_plt, runner, tmp_path, monkeypatch):
    """Test plotting account trend data with a custom title."""
    monkeypatch.chdir(tmp_path)

    # Create a test CSV file
    test_data = [
        {"star_date": "2023-01-01", "total_new_stars_on_day": 2, "total_cumulative_stars_up_to_day": 2},
        {"star_date": "2023-01-02", "total_new_stars_on_day": 1, "total_cumulative_stars_up_to_day": 3},
    ]
    test_csv = tmp_path / "stars.csv"
    pd.DataFrame(test_data).to_csv(test_csv, index=False)

    custom_title = "My Custom Plot Title"
    result = runner.invoke(
        cli,
        ["plot", "--file", str(test_csv), "--type", "account-trend", "--title", custom_title],
        catch_exceptions=False,
    )
    assert result.exit_code == 0, f"CLI Error: {result.output}"

    mock_plt.title.assert_called_once_with(custom_title)


def test_plot_command_invalid_file(runner, tmp_path, monkeypatch):
    """Test plotting with a non-existent file."""
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(cli, ["plot", "--file", "nonexistent.csv", "--type", "account-trend"])
    assert result.exit_code == 2  # Click's error code for file not found
    assert "does not exist" in result.output


def test_plot_command_invalid_csv(runner, tmp_path, monkeypatch):
    """Test plotting with a CSV missing required columns."""
    monkeypatch.chdir(tmp_path)

    # Create a CSV with wrong columns
    test_data = [{"wrong_column": 1}]
    test_csv = tmp_path / "invalid.csv"
    pd.DataFrame(test_data).to_csv(test_csv, index=False)

    result = runner.invoke(cli, ["plot", "--file", str(test_csv), "--type", "account-trend"])
    assert result.exit_code == 1
    assert "CSV file must contain columns" in result.output


# --- Traffic command tests ---


def mock_traffic_views_api(httpx_mock, repo_full_name, views_data):
    """Helper to mock the /repos/{repo}/traffic/views endpoint."""
    url = f"{BASE_API_URL}/repos/{repo_full_name}/traffic/views"
    httpx_mock.add_response(url=url, method="GET", json=views_data, status_code=200)


def mock_traffic_clones_api(httpx_mock, repo_full_name, clones_data):
    """Helper to mock the /repos/{repo}/traffic/clones endpoint."""
    url = f"{BASE_API_URL}/repos/{repo_full_name}/traffic/clones"
    httpx_mock.add_response(url=url, method="GET", json=clones_data, status_code=200)


def mock_traffic_referrers_api(httpx_mock, repo_full_name, referrers_data):
    """Helper to mock the /repos/{repo}/traffic/popular/referrers endpoint."""
    url = f"{BASE_API_URL}/repos/{repo_full_name}/traffic/popular/referrers"
    httpx_mock.add_response(url=url, method="GET", json=referrers_data, status_code=200)


def test_fetch_traffic_views(httpx_mock):
    repo = "testowner/testrepo"
    views_response = {
        "count": 100,
        "uniques": 50,
        "views": [
            {"timestamp": "2023-01-01T00:00:00Z", "count": 60, "uniques": 30},
            {"timestamp": "2023-01-02T00:00:00Z", "count": 40, "uniques": 20},
        ],
    }
    mock_traffic_views_api(httpx_mock, repo, views_response)
    result = fetch_traffic_views(repo)
    assert result is not None
    assert result["count"] == 100
    assert result["uniques"] == 50
    assert len(result["views"]) == 2


def test_fetch_traffic_views_no_access(httpx_mock):
    repo = "testowner/private_repo"
    url = f"{BASE_API_URL}/repos/{repo}/traffic/views"
    httpx_mock.add_response(url=url, method="GET", json={"message": "Forbidden"}, status_code=403)
    result = fetch_traffic_views(repo)
    assert result is None


@pytest.mark.parametrize(
    ("fetcher", "endpoint", "payload"),
    [
        (fetch_traffic_views, "views", {"count": 101, "uniques": 51, "views": []}),
        (fetch_traffic_clones, "clones", {"count": 26, "uniques": 11, "clones": []}),
        (
            fetch_traffic_referrers,
            "popular/referrers",
            [{"referrer": "github.com", "count": 81, "uniques": 41}],
        ),
    ],
)
def test_fetch_traffic_retries_rate_limit(httpx_mock, monkeypatch, fetcher, endpoint, payload):
    repo = "testowner/testrepo"
    url = f"{BASE_API_URL}/repos/{repo}/traffic/{endpoint}"
    monkeypatch.setattr("stargazers.cli.time.sleep", lambda _seconds: None)
    httpx_mock.add_response(
        url=url,
        method="GET",
        status_code=403,
        text="API rate limit exceeded for user ID 1.",
        headers={"X-RateLimit-Reset": str(int(time.time()) + 5)},
    )
    httpx_mock.add_response(url=url, method="GET", status_code=200, json=payload)

    result = fetcher(repo)

    assert len([request for request in httpx_mock.get_requests() if str(request.url) == url]) == 2
    if endpoint == "popular/referrers":
        assert result[0]["count"] == 81
        assert result[0]["uniques"] == 41
    else:
        assert result["count"] == payload["count"]
        assert result["uniques"] == payload["uniques"]


@pytest.mark.parametrize(
    ("fetcher", "endpoint"),
    [
        (fetch_traffic_views, "views"),
        (fetch_traffic_clones, "clones"),
        (fetch_traffic_referrers, "popular/referrers"),
    ],
)
def test_fetch_traffic_persistent_rate_limit_warns(httpx_mock, monkeypatch, fetcher, endpoint):
    capturing = CapturingConsole()
    monkeypatch.setattr("stargazers.cli.console", capturing)
    monkeypatch.setattr("stargazers.cli.time.sleep", lambda _seconds: None)
    repo = "testowner/testrepo"
    url = f"{BASE_API_URL}/repos/{repo}/traffic/{endpoint}"
    httpx_mock.add_response(
        url=url,
        method="GET",
        status_code=403,
        text="You have exceeded a secondary rate limit.",
        headers={"X-RateLimit-Reset": str(int(time.time()) + 5)},
        is_reusable=True,
    )

    result = fetcher(repo)

    assert result is None
    assert len([request for request in httpx_mock.get_requests() if str(request.url) == url]) == 2
    assert any("Rate limited fetching traffic" in message and repo in message for message in capturing.messages)
    assert not any("No push access" in message for message in capturing.messages)


def test_fetch_traffic_clones(httpx_mock):
    repo = "testowner/testrepo"
    clones_response = {
        "count": 25,
        "uniques": 10,
        "clones": [
            {"timestamp": "2023-01-01T00:00:00Z", "count": 15, "uniques": 6},
            {"timestamp": "2023-01-02T00:00:00Z", "count": 10, "uniques": 4},
        ],
    }
    mock_traffic_clones_api(httpx_mock, repo, clones_response)
    result = fetch_traffic_clones(repo)
    assert result is not None
    assert result["count"] == 25
    assert result["uniques"] == 10


def test_fetch_traffic_referrers(httpx_mock):
    repo = "testowner/testrepo"
    referrers_response = [
        {"referrer": "github.com", "count": 80, "uniques": 40},
        {"referrer": "google.com", "count": 20, "uniques": 10},
    ]
    mock_traffic_referrers_api(httpx_mock, repo, referrers_response)
    result = fetch_traffic_referrers(repo)
    assert result is not None
    assert len(result) == 2
    assert result[0]["referrer"] == "github.com"


def test_traffic_command(runner, httpx_mock_non_strict_assertion, tmp_path, monkeypatch):
    httpx_mock = httpx_mock_non_strict_assertion
    username = "testuser"
    monkeypatch.chdir(tmp_path)

    mock_user_repos_api(
        httpx_mock,
        username,
        [
            {"full_name": "testuser/repo1", "owner": {"login": username}},
            {"full_name": "testuser/repo2", "owner": {"login": username}},
        ],
    )

    # Mock traffic endpoints for repo1
    mock_traffic_views_api(httpx_mock, "testuser/repo1", {"count": 100, "uniques": 50, "views": []})
    mock_traffic_clones_api(httpx_mock, "testuser/repo1", {"count": 20, "uniques": 10, "clones": []})
    mock_traffic_referrers_api(
        httpx_mock,
        "testuser/repo1",
        [
            {"referrer": "github.com", "count": 60, "uniques": 30},
            {"referrer": "google.com", "count": 40, "uniques": 20},
        ],
    )

    # Mock traffic endpoints for repo2
    mock_traffic_views_api(httpx_mock, "testuser/repo2", {"count": 50, "uniques": 25, "views": []})
    mock_traffic_clones_api(httpx_mock, "testuser/repo2", {"count": 5, "uniques": 3, "clones": []})
    mock_traffic_referrers_api(
        httpx_mock,
        "testuser/repo2",
        [
            {"referrer": "github.com", "count": 30, "uniques": 15},
            {"referrer": "reddit.com", "count": 20, "uniques": 10},
        ],
    )

    result = runner.invoke(cli, ["traffic", username], catch_exceptions=False)
    assert result.exit_code == 0, f"CLI Error: {result.output}"

    # Check output files
    traffic_file = tmp_path / f"{username}_traffic.csv"
    assert traffic_file.exists()
    data = read_csv_output(traffic_file)
    assert len(data) == 2
    # repo1 has more views, should be first
    assert data[0]["repo"] == "testuser/repo1"
    assert data[0]["views"] == "100"
    assert data[1]["repo"] == "testuser/repo2"
    assert data[1]["views"] == "50"

    referrers_file = tmp_path / f"{username}_referrers.csv"
    assert referrers_file.exists()
    ref_data = read_csv_output(referrers_file)
    assert len(ref_data) == 3
    # github.com should be first (60+30=90)
    assert ref_data[0]["referrer"] == "github.com"
    assert ref_data[0]["count"] == "90"
    assert ref_data[0]["uniques"] == "45"


def test_traffic_command_by_day(runner, httpx_mock_non_strict_assertion, tmp_path, monkeypatch):
    """The per-day breakdown is joined on (repo, date) and costs no extra requests."""
    httpx_mock = httpx_mock_non_strict_assertion
    username = "testuser"
    monkeypatch.chdir(tmp_path)

    mock_user_repos_api(
        httpx_mock,
        username,
        [
            {"full_name": "testuser/repo1", "owner": {"login": username}},
            {"full_name": "testuser/repo2", "owner": {"login": username}},
        ],
    )

    # repo1: views on the 18th/19th/20th, clones on the 19th/21st — the 18th, 20th and 21st
    # each appear in exactly one array and must be zero-filled on the other side.
    mock_traffic_views_api(
        httpx_mock,
        "testuser/repo1",
        {
            "count": 23,
            "uniques": 13,
            "views": [
                {"timestamp": "2026-07-18T00:00:00Z", "count": 5, "uniques": 3},
                {"timestamp": "2026-07-19T00:00:00Z", "count": 7, "uniques": 4},
                {"timestamp": "2026-07-20T00:00:00Z", "count": 11, "uniques": 6},
            ],
        },
    )
    mock_traffic_clones_api(
        httpx_mock,
        "testuser/repo1",
        {
            "count": 30,
            "uniques": 17,
            "clones": [
                {"timestamp": "2026-07-19T00:00:00Z", "count": 13, "uniques": 8},
                {"timestamp": "2026-07-21T00:00:00Z", "count": 17, "uniques": 9},
            ],
        },
    )
    mock_traffic_referrers_api(httpx_mock, "testuser/repo1", [])

    # repo2 shares the 19th and the 21st with repo1 but reports different numbers, so a
    # date-only (repo-blind) join would merge rows and change the values.
    mock_traffic_views_api(
        httpx_mock,
        "testuser/repo2",
        {
            "count": 52,
            "uniques": 26,
            "views": [
                {"timestamp": "2026-07-19T00:00:00Z", "count": 23, "uniques": 12},
                {"timestamp": "2026-07-21T00:00:00Z", "count": 29, "uniques": 14},
            ],
        },
    )
    mock_traffic_clones_api(
        httpx_mock,
        "testuser/repo2",
        {
            "count": 31,
            "uniques": 16,
            "clones": [{"timestamp": "2026-07-19T00:00:00Z", "count": 31, "uniques": 16}],
        },
    )
    mock_traffic_referrers_api(httpx_mock, "testuser/repo2", [])

    result = runner.invoke(cli, ["traffic", username], catch_exceptions=False)
    assert result.exit_code == 0, f"CLI Error: {result.output}"

    # Same request list as test_traffic_command: one repos page plus three per repository.
    assert [str(r.url) for r in httpx_mock.get_requests()] == [
        f"{BASE_API_URL}/users/{username}/repos?type=owner&sort=full_name&per_page={PER_PAGE}&page=1",
        f"{BASE_API_URL}/repos/testuser/repo1/traffic/views",
        f"{BASE_API_URL}/repos/testuser/repo1/traffic/clones",
        f"{BASE_API_URL}/repos/testuser/repo1/traffic/popular/referrers",
        f"{BASE_API_URL}/repos/testuser/repo2/traffic/views",
        f"{BASE_API_URL}/repos/testuser/repo2/traffic/clones",
        f"{BASE_API_URL}/repos/testuser/repo2/traffic/popular/referrers",
    ]

    by_day_file = tmp_path / f"{username}_traffic_by_day.csv"
    with open(by_day_file, encoding="utf-8") as f:
        assert next(csv.reader(f)) == ["date", "repo", "views", "unique_views", "clones", "unique_clones"]

    by_day = read_csv_output(by_day_file)
    assert by_day == [
        {
            "date": "2026-07-18",
            "repo": "testuser/repo1",
            "views": "5",
            "unique_views": "3",
            "clones": "0",
            "unique_clones": "0",
        },
        {
            "date": "2026-07-19",
            "repo": "testuser/repo1",
            "views": "7",
            "unique_views": "4",
            "clones": "13",
            "unique_clones": "8",
        },
        {
            "date": "2026-07-19",
            "repo": "testuser/repo2",
            "views": "23",
            "unique_views": "12",
            "clones": "31",
            "unique_clones": "16",
        },
        {
            "date": "2026-07-20",
            "repo": "testuser/repo1",
            "views": "11",
            "unique_views": "6",
            "clones": "0",
            "unique_clones": "0",
        },
        {
            "date": "2026-07-21",
            "repo": "testuser/repo1",
            "views": "0",
            "unique_views": "0",
            "clones": "17",
            "unique_clones": "9",
        },
        {
            "date": "2026-07-21",
            "repo": "testuser/repo2",
            "views": "29",
            "unique_views": "14",
            "clones": "0",
            "unique_clones": "0",
        },
    ]

    # The roll-up CSV still carries GitHub's own totals, unchanged.
    assert read_csv_output(tmp_path / f"{username}_traffic.csv") == [
        {"repo": "testuser/repo2", "views": "52", "unique_views": "26", "clones": "31", "unique_clones": "16"},
        {"repo": "testuser/repo1", "views": "23", "unique_views": "13", "clones": "30", "unique_clones": "17"},
    ]


def test_traffic_command_by_day_clones_unavailable(runner, httpx_mock_non_strict_assertion, tmp_path, monkeypatch):
    """A repository whose clones fetch fails contributes views rows with BLANK clone columns."""
    httpx_mock = httpx_mock_non_strict_assertion
    username = "testuser"
    monkeypatch.chdir(tmp_path)

    mock_user_repos_api(
        httpx_mock,
        username,
        [
            {"full_name": "testuser/repo1", "owner": {"login": username}},
            {"full_name": "testuser/repo2", "owner": {"login": username}},
        ],
    )

    mock_traffic_views_api(
        httpx_mock,
        "testuser/repo1",
        {"count": 5, "uniques": 3, "views": [{"timestamp": "2026-07-18T00:00:00Z", "count": 5, "uniques": 3}]},
    )
    # Plain 403: the non-rate-limit skip branch, so fetch_traffic_clones returns None.
    httpx_mock.add_response(
        url=f"{BASE_API_URL}/repos/testuser/repo1/traffic/clones",
        method="GET",
        json={"message": "Forbidden"},
        status_code=403,
    )
    mock_traffic_referrers_api(httpx_mock, "testuser/repo1", [])

    mock_traffic_views_api(
        httpx_mock,
        "testuser/repo2",
        {"count": 7, "uniques": 4, "views": [{"timestamp": "2026-07-18T00:00:00Z", "count": 7, "uniques": 4}]},
    )
    mock_traffic_clones_api(
        httpx_mock,
        "testuser/repo2",
        {"count": 9, "uniques": 5, "clones": [{"timestamp": "2026-07-18T00:00:00Z", "count": 9, "uniques": 5}]},
    )
    mock_traffic_referrers_api(httpx_mock, "testuser/repo2", [])

    result = runner.invoke(cli, ["traffic", username], catch_exceptions=False)
    assert result.exit_code == 0, f"CLI Error: {result.output}"

    by_day = read_csv_output(tmp_path / f"{username}_traffic_by_day.csv")
    assert by_day == [
        {
            "date": "2026-07-18",
            "repo": "testuser/repo1",
            "views": "5",
            "unique_views": "3",
            "clones": "",
            "unique_clones": "",
        },
        {
            "date": "2026-07-18",
            "repo": "testuser/repo2",
            "views": "7",
            "unique_views": "4",
            "clones": "9",
            "unique_clones": "5",
        },
    ]


def test_traffic_command_by_day_all_clones_unavailable(runner, httpx_mock_non_strict_assertion, tmp_path, monkeypatch):
    """Every row's clone cells are missing — the nullable-int cast must still render blanks, not crash."""
    httpx_mock = httpx_mock_non_strict_assertion
    username = "testuser"
    monkeypatch.chdir(tmp_path)

    mock_user_repos_api(httpx_mock, username, [{"full_name": "testuser/repo1", "owner": {"login": username}}])
    mock_traffic_views_api(
        httpx_mock,
        "testuser/repo1",
        {"count": 5, "uniques": 3, "views": [{"timestamp": "2026-07-18T00:00:00Z", "count": 5, "uniques": 3}]},
    )
    httpx_mock.add_response(
        url=f"{BASE_API_URL}/repos/testuser/repo1/traffic/clones",
        method="GET",
        json={"message": "Forbidden"},
        status_code=403,
    )
    mock_traffic_referrers_api(httpx_mock, "testuser/repo1", [])

    result = runner.invoke(cli, ["traffic", username], catch_exceptions=False)
    assert result.exit_code == 0, f"CLI Error: {result.output}"

    assert read_csv_output(tmp_path / f"{username}_traffic_by_day.csv") == [
        {
            "date": "2026-07-18",
            "repo": "testuser/repo1",
            "views": "5",
            "unique_views": "3",
            "clones": "",
            "unique_clones": "",
        }
    ]


def test_traffic_command_by_day_not_written_without_daily_rows(
    runner, httpx_mock_non_strict_assertion, tmp_path, monkeypatch
):
    """No repository yielded a daily row -> no per-day CSV, while the roll-up is still written."""
    httpx_mock = httpx_mock_non_strict_assertion
    username = "testuser"
    monkeypatch.chdir(tmp_path)

    mock_user_repos_api(httpx_mock, username, [{"full_name": "testuser/repo1", "owner": {"login": username}}])
    mock_traffic_views_api(httpx_mock, "testuser/repo1", {"count": 0, "uniques": 0, "views": []})
    mock_traffic_clones_api(httpx_mock, "testuser/repo1", {"count": 0, "uniques": 0, "clones": []})
    mock_traffic_referrers_api(httpx_mock, "testuser/repo1", [])

    result = runner.invoke(cli, ["traffic", username], catch_exceptions=False)
    assert result.exit_code == 0, f"CLI Error: {result.output}"

    assert (tmp_path / f"{username}_traffic.csv").exists()
    assert not (tmp_path / f"{username}_traffic_by_day.csv").exists()


def test_traffic_command_with_exclude(runner, httpx_mock_non_strict_assertion, tmp_path, monkeypatch):
    httpx_mock = httpx_mock_non_strict_assertion
    username = "testuser"
    monkeypatch.chdir(tmp_path)

    mock_user_repos_api(
        httpx_mock,
        username,
        [
            {"full_name": "testuser/repo1", "owner": {"login": username}},
            {"full_name": "testuser/repo2", "owner": {"login": username}},
        ],
    )

    # Only mock repo1 traffic (repo2 is excluded)
    mock_traffic_views_api(httpx_mock, "testuser/repo1", {"count": 100, "uniques": 50, "views": []})
    mock_traffic_clones_api(httpx_mock, "testuser/repo1", {"count": 20, "uniques": 10, "clones": []})
    mock_traffic_referrers_api(httpx_mock, "testuser/repo1", [])

    result = runner.invoke(cli, ["traffic", username, "--exclude-repo", "testuser/repo2"], catch_exceptions=False)
    assert result.exit_code == 0, f"CLI Error: {result.output}"

    traffic_file = tmp_path / f"{username}_traffic.csv"
    assert traffic_file.exists()
    data = read_csv_output(traffic_file)
    assert len(data) == 1
    assert data[0]["repo"] == "testuser/repo1"


def test_traffic_command_invalid_include_repo(runner, httpx_mock_non_strict_assertion, tmp_path, monkeypatch):
    """A malformed --include-repo is warned about and dropped; owned repos still process."""
    httpx_mock = httpx_mock_non_strict_assertion
    username = "testuser"
    monkeypatch.chdir(tmp_path)
    capturing = CapturingConsole()
    monkeypatch.setattr("stargazers.cli.console", capturing)

    mock_user_repos_api(httpx_mock, username, [{"full_name": "testuser/repo1", "owner": {"login": username}}])
    mock_traffic_views_api(httpx_mock, "testuser/repo1", {"count": 100, "uniques": 50, "views": []})
    mock_traffic_clones_api(httpx_mock, "testuser/repo1", {"count": 20, "uniques": 10, "clones": []})
    mock_traffic_referrers_api(httpx_mock, "testuser/repo1", [])

    # "notarepo" has no slash — it must never reach the traffic fetchers (no mock for it).
    result = runner.invoke(cli, ["traffic", username, "--include-repo", "notarepo"], catch_exceptions=False)
    assert result.exit_code == 0, f"CLI Error: {result.output}"

    assert any("Invalid repository format: 'notarepo'" in m for m in capturing.messages)

    # The valid owned repo is still processed and the traffic CSV is written.
    traffic_file = tmp_path / f"{username}_traffic.csv"
    assert traffic_file.exists()
    data = read_csv_output(traffic_file)
    assert len(data) == 1
    assert data[0]["repo"] == "testuser/repo1"
    assert data[0]["views"] == "100"


def test_traffic_command_skips_no_access(runner, httpx_mock_non_strict_assertion, tmp_path, monkeypatch):
    httpx_mock = httpx_mock_non_strict_assertion
    username = "testuser"
    monkeypatch.chdir(tmp_path)

    mock_user_repos_api(
        httpx_mock,
        username,
        [
            {"full_name": "testuser/repo1", "owner": {"login": username}},
            {"full_name": "testuser/noaccess", "owner": {"login": username}},
        ],
    )

    # repo1 works fine
    mock_traffic_views_api(httpx_mock, "testuser/repo1", {"count": 50, "uniques": 25, "views": []})
    mock_traffic_clones_api(httpx_mock, "testuser/repo1", {"count": 10, "uniques": 5, "clones": []})
    mock_traffic_referrers_api(httpx_mock, "testuser/repo1", [])

    # noaccess returns 403
    url_views = f"{BASE_API_URL}/repos/testuser/noaccess/traffic/views"
    httpx_mock.add_response(url=url_views, method="GET", json={"message": "Forbidden"}, status_code=403)

    result = runner.invoke(cli, ["traffic", username], catch_exceptions=False)
    assert result.exit_code == 0, f"CLI Error: {result.output}"

    traffic_file = tmp_path / f"{username}_traffic.csv"
    assert traffic_file.exists()
    data = read_csv_output(traffic_file)
    assert len(data) == 1
    assert data[0]["repo"] == "testuser/repo1"


def test_traffic_command_skips_missing_repo(runner, httpx_mock_non_strict_assertion, tmp_path, monkeypatch):
    httpx_mock = httpx_mock_non_strict_assertion
    username = "testuser"
    monkeypatch.chdir(tmp_path)
    capturing = CapturingConsole()
    monkeypatch.setattr("stargazers.cli.console", capturing)

    mock_user_repos_api(
        httpx_mock,
        username,
        [
            {"full_name": "testuser/repo1", "owner": {"login": username}},
            {"full_name": "testuser/missing", "owner": {"login": username}},
        ],
    )
    mock_traffic_views_api(httpx_mock, "testuser/repo1", {"count": 50, "uniques": 25, "views": []})
    mock_traffic_clones_api(httpx_mock, "testuser/repo1", {"count": 10, "uniques": 5, "clones": []})
    mock_traffic_referrers_api(httpx_mock, "testuser/repo1", [])
    httpx_mock.add_response(
        url=f"{BASE_API_URL}/repos/testuser/missing/traffic/views",
        method="GET",
        status_code=404,
        json={"message": "Not Found"},
    )

    result = runner.invoke(cli, ["traffic", username], catch_exceptions=False)
    assert result.exit_code == 0

    data = read_csv_output(tmp_path / f"{username}_traffic.csv")
    assert data == [
        {
            "repo": "testuser/repo1",
            "views": "50",
            "unique_views": "25",
            "clones": "10",
            "unique_clones": "5",
        }
    ]
    assert any("Repository not found:" in m and "testuser/missing" in m for m in capturing.messages)
    assert any("Repos analyzed: 1 (skipped 1 due to access or missing repositories)" in m for m in capturing.messages)


def test_traffic_command_warns_when_clones_unavailable(runner, httpx_mock_non_strict_assertion, tmp_path, monkeypatch):
    """Views succeed but clones fail: the row keeps the 0 fill, and an undercount warning names the repo."""
    httpx_mock = httpx_mock_non_strict_assertion
    username = "testuser"
    monkeypatch.chdir(tmp_path)
    capturing = CapturingConsole()
    monkeypatch.setattr("stargazers.cli.console", capturing)

    mock_user_repos_api(
        httpx_mock,
        username,
        [
            {"full_name": "testuser/repo1", "owner": {"login": username}},
            {"full_name": "testuser/repo2", "owner": {"login": username}},
        ],
    )

    # repo1 is fully readable.
    mock_traffic_views_api(httpx_mock, "testuser/repo1", {"count": 50, "uniques": 25, "views": []})
    mock_traffic_clones_api(httpx_mock, "testuser/repo1", {"count": 10, "uniques": 5, "clones": []})
    mock_traffic_referrers_api(httpx_mock, "testuser/repo1", [])

    # repo2's views succeed, so it is analyzed rather than skipped, but its clones call fails.
    mock_traffic_views_api(httpx_mock, "testuser/repo2", {"count": 40, "uniques": 20, "views": []})
    httpx_mock.add_response(
        url=f"{BASE_API_URL}/repos/testuser/repo2/traffic/clones",
        method="GET",
        status_code=403,
        json={"message": "Forbidden"},
    )
    mock_traffic_referrers_api(httpx_mock, "testuser/repo2", [])

    result = runner.invoke(cli, ["traffic", username], catch_exceptions=False)
    assert result.exit_code == 0, f"CLI Error: {result.output}"

    assert any(
        "WARNING: clone data was unavailable for 1 repo(s) (testuser/repo2) — "
        "clone totals UNDERCOUNT. Re-run to get complete data." in message
        for message in capturing.messages
    )
    # Only the clones fetch failed, so the referrer warning must stay silent.
    assert not any("referrer data was unavailable" in message for message in capturing.messages)

    # The skipped count keeps its existing meaning: repo2 was analyzed, not skipped.
    assert any("Repos analyzed: 2 (skipped 0 due to access or missing repositories)" in m for m in capturing.messages)

    # CSV schema and the 0 fill are deliberately unchanged.
    data = read_csv_output(tmp_path / f"{username}_traffic.csv")
    assert data == [
        {"repo": "testuser/repo1", "views": "50", "unique_views": "25", "clones": "10", "unique_clones": "5"},
        {"repo": "testuser/repo2", "views": "40", "unique_views": "20", "clones": "0", "unique_clones": "0"},
    ]


def test_traffic_command_warns_when_referrers_unavailable(
    runner, httpx_mock_non_strict_assertion, tmp_path, monkeypatch
):
    """A failed referrers fetch is named in its own undercount warning."""
    httpx_mock = httpx_mock_non_strict_assertion
    username = "testuser"
    monkeypatch.chdir(tmp_path)
    capturing = CapturingConsole()
    monkeypatch.setattr("stargazers.cli.console", capturing)

    mock_user_repos_api(httpx_mock, username, [{"full_name": "testuser/repo1", "owner": {"login": username}}])
    mock_traffic_views_api(httpx_mock, "testuser/repo1", {"count": 50, "uniques": 25, "views": []})
    mock_traffic_clones_api(httpx_mock, "testuser/repo1", {"count": 10, "uniques": 5, "clones": []})
    httpx_mock.add_response(
        url=f"{BASE_API_URL}/repos/testuser/repo1/traffic/popular/referrers",
        method="GET",
        status_code=403,
        json={"message": "Forbidden"},
    )

    result = runner.invoke(cli, ["traffic", username], catch_exceptions=False)
    assert result.exit_code == 0, f"CLI Error: {result.output}"

    assert any(
        "WARNING: referrer data was unavailable for 1 repo(s) (testuser/repo1) — "
        "referrer totals UNDERCOUNT. Re-run to get complete data." in message
        for message in capturing.messages
    )
    assert not any("clone data was unavailable" in message for message in capturing.messages)


def test_traffic_command_no_warning_when_all_fetches_succeed(
    runner, httpx_mock_non_strict_assertion, tmp_path, monkeypatch
):
    """A fully successful run prints neither undercount warning, including when referrers are legitimately empty."""
    httpx_mock = httpx_mock_non_strict_assertion
    username = "testuser"
    monkeypatch.chdir(tmp_path)
    capturing = CapturingConsole()
    monkeypatch.setattr("stargazers.cli.console", capturing)

    mock_user_repos_api(
        httpx_mock,
        username,
        [
            {"full_name": "testuser/repo1", "owner": {"login": username}},
            {"full_name": "testuser/repo2", "owner": {"login": username}},
        ],
    )
    mock_traffic_views_api(httpx_mock, "testuser/repo1", {"count": 50, "uniques": 25, "views": []})
    mock_traffic_clones_api(httpx_mock, "testuser/repo1", {"count": 10, "uniques": 5, "clones": []})
    mock_traffic_referrers_api(httpx_mock, "testuser/repo1", [{"referrer": "github.com", "count": 5, "uniques": 3}])
    mock_traffic_views_api(httpx_mock, "testuser/repo2", {"count": 40, "uniques": 20, "views": []})
    # A repo with zero clones and no referrers at all is genuine data, not a failure.
    mock_traffic_clones_api(httpx_mock, "testuser/repo2", {"count": 0, "uniques": 0, "clones": []})
    mock_traffic_referrers_api(httpx_mock, "testuser/repo2", [])

    result = runner.invoke(cli, ["traffic", username], catch_exceptions=False)
    assert result.exit_code == 0, f"CLI Error: {result.output}"

    assert not any("UNDERCOUNT" in message for message in capturing.messages)


OVERVIEW_REPOS_BASE_URL = f"{BASE_API_URL}/users/testuser/repos?type=owner&sort=full_name&per_page={PER_PAGE}"


def _repo_snapshot(full_name, owner="testuser", **overrides):
    """Build a repository-list payload item shaped like the live /users/{u}/repos response."""
    payload = {
        "full_name": full_name,
        "owner": {"login": owner},
        "description": f"about {full_name}",
        "language": "Python",
        "topics": ["cli"],
        "license": {"spdx_id": "MIT"},
        "stargazers_count": 1,
        # An alias for the star count on this payload; the CSV must not carry it.
        "watchers_count": 1,
        "forks_count": 0,
        "open_issues_count": 0,
        "size": 10,
        "fork": False,
        "archived": False,
        "created_at": "2020-01-01T00:00:00Z",
        "pushed_at": "2024-01-01T00:00:00Z",
        "homepage": None,
    }
    payload.update(overrides)
    return payload


def mock_user_repos_pages(httpx_mock, username, pages):
    """Mock the owned-repositories endpoint across several linked pages."""
    base = f"{BASE_API_URL}/users/{username}/repos?type=owner&sort=full_name&per_page={PER_PAGE}"
    for index, page in enumerate(pages, start=1):
        headers = {"Link": f'<{base}&page={index + 1}>; rel="next"'} if index < len(pages) else {}
        httpx_mock.add_response(url=f"{base}&page={index}", method="GET", json=page, status_code=200, headers=headers)


def _overview_fixture_pages():
    """Two pages whose per-repository numbers all differ, so a wrong aggregation cannot pass."""
    return [
        [
            _repo_snapshot(
                "testuser/alpha",
                topics=["cli", "analytics"],
                stargazers_count=30,
                watchers_count=30,
                forks_count=4,
                open_issues_count=2,
                size=120,
                pushed_at="2024-05-01T00:00:00Z",
                homepage="https://alpha.example",
            ),
            _repo_snapshot(
                "testuser/beta",
                description=None,
                topics=[],
                license=None,
                stargazers_count=7,
                watchers_count=7,
                forks_count=1,
                size=12,
                archived=True,
                created_at="2021-02-03T00:00:00Z",
                pushed_at="2022-03-04T00:00:00Z",
            ),
            _repo_snapshot("otheracct/not-owned", owner="otheracct", stargazers_count=999),
        ],
        [
            _repo_snapshot(
                "testuser/gamma",
                language="Rust",
                topics=["rust"],
                license={"spdx_id": "Apache-2.0"},
                stargazers_count=12,
                watchers_count=12,
                forks_count=9,
                open_issues_count=5,
                size=300,
                fork=True,
                archived=True,
                created_at="2022-06-07T00:00:00Z",
                pushed_at="2023-08-09T00:00:00Z",
            ),
        ],
    ]


def test_overview_command_writes_exact_rows(runner, httpx_mock, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    mock_user_repos_pages(httpx_mock, "testuser", _overview_fixture_pages())

    result = runner.invoke(cli, ["overview", "testuser"], catch_exceptions=False)

    assert result.exit_code == 0, f"CLI Error: {result.output}"
    with open(tmp_path / "testuser_repos_overview.csv", encoding="utf-8") as csv_file:
        reader = csv.DictReader(csv_file)
        assert reader.fieldnames == [
            "repo",
            "description",
            "language",
            "topics",
            "license",
            "stars",
            "forks",
            "open_issues",
            "size_kb",
            "is_fork",
            "archived",
            "created_at",
            "pushed_at",
            "homepage",
        ]
        rows = [dict(row) for row in reader]

    assert rows == [
        {
            "repo": "testuser/alpha",
            "description": "about testuser/alpha",
            "language": "Python",
            "topics": "cli, analytics",
            "license": "MIT",
            "stars": "30",
            "forks": "4",
            "open_issues": "2",
            "size_kb": "120",
            "is_fork": "False",
            "archived": "False",
            "created_at": "2020-01-01T00:00:00Z",
            "pushed_at": "2024-05-01T00:00:00Z",
            "homepage": "https://alpha.example",
        },
        {
            "repo": "testuser/gamma",
            "description": "about testuser/gamma",
            "language": "Rust",
            "topics": "rust",
            "license": "Apache-2.0",
            "stars": "12",
            "forks": "9",
            "open_issues": "5",
            "size_kb": "300",
            "is_fork": "True",
            "archived": "True",
            "created_at": "2022-06-07T00:00:00Z",
            "pushed_at": "2023-08-09T00:00:00Z",
            "homepage": "",
        },
        {
            "repo": "testuser/beta",
            "description": "",
            "language": "Python",
            "topics": "",
            "license": "",
            "stars": "7",
            "forks": "1",
            "open_issues": "0",
            "size_kb": "12",
            "is_fork": "False",
            "archived": "True",
            "created_at": "2021-02-03T00:00:00Z",
            "pushed_at": "2022-03-04T00:00:00Z",
            "homepage": "",
        },
    ]


def test_overview_command_makes_no_extra_requests(runner, httpx_mock, tmp_path, monkeypatch):
    """The snapshot comes off the repository list itself — nothing else may be requested."""
    monkeypatch.chdir(tmp_path)
    mock_user_repos_pages(httpx_mock, "testuser", _overview_fixture_pages())

    result = runner.invoke(cli, ["overview", "testuser"], catch_exceptions=False)

    assert result.exit_code == 0, f"CLI Error: {result.output}"
    assert [str(request.url) for request in httpx_mock.get_requests()] == [
        f"{OVERVIEW_REPOS_BASE_URL}&page=1",
        f"{OVERVIEW_REPOS_BASE_URL}&page=2",
    ]


def test_overview_command_excludes_other_owners_and_excluded_repos(runner, httpx_mock, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    mock_user_repos_pages(httpx_mock, "testuser", _overview_fixture_pages())

    result = runner.invoke(cli, ["overview", "testuser", "--exclude-repo", "testuser/gamma"], catch_exceptions=False)

    assert result.exit_code == 0, f"CLI Error: {result.output}"
    rows = read_csv_output(tmp_path / "testuser_repos_overview.csv")
    assert [row["repo"] for row in rows] == ["testuser/alpha", "testuser/beta"]


def test_overview_command_warns_for_unowned_include_repo(runner, httpx_mock, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    capturing = CapturingConsole()
    monkeypatch.setattr("stargazers.cli.console", capturing)
    mock_user_repos_pages(httpx_mock, "testuser", _overview_fixture_pages())

    result = runner.invoke(
        cli, ["overview", "testuser", "--include-repo", "otheracct/elsewhere"], catch_exceptions=False
    )

    assert result.exit_code == 0, f"CLI Error: {result.output}"
    assert any(
        "otheracct/elsewhere" in message and "owned repositories only" in message for message in capturing.messages
    )
    rows = read_csv_output(tmp_path / "testuser_repos_overview.csv")
    assert [row["repo"] for row in rows] == ["testuser/alpha", "testuser/gamma", "testuser/beta"]


def test_overview_command_summary(runner, httpx_mock, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    capturing = CapturingConsole()
    monkeypatch.setattr("stargazers.cli.console", capturing)
    mock_user_repos_pages(httpx_mock, "testuser", _overview_fixture_pages())

    result = runner.invoke(cli, ["overview", "testuser"], catch_exceptions=False)

    assert result.exit_code == 0, f"CLI Error: {result.output}"
    assert "Total repositories: 3" in capturing.messages
    assert "Forks: 1" in capturing.messages
    assert "Archived: 2" in capturing.messages
    assert "Total stars: 49" in capturing.messages
    assert "Total forks: 14" in capturing.messages
    assert "testuser/alpha: 30 stars" in capturing.messages
    assert "testuser/gamma: 12 stars" in capturing.messages
    assert "testuser/beta: 7 stars" in capturing.messages
    assert "Python: 2 repositories" in capturing.messages
    assert "Rust: 1 repositories" in capturing.messages


def test_cli_help_lists_overview_command(runner):
    result = runner.invoke(cli, ["--help"], catch_exceptions=False)

    assert result.exit_code == 0
    assert "overview" in result.output


def test_overview_help_documents_options(runner):
    result = runner.invoke(cli, ["overview", "--help"], catch_exceptions=False)

    assert result.exit_code == 0
    assert "USERNAME" in result.output
    assert "--include-repo" in result.output
    assert "--exclude-repo" in result.output
