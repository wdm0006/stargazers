import csv
import os
import sys
import time

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
    fetch_forkers,
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
