import csv
import os
import sys

import pandas as pd

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from unittest.mock import patch

import pytest
from click.testing import CliRunner
from pytest_httpx import HTTPXMock  # Import HTTPXMock for type hinting

from stargazers.cli import (
    cli,
    fetch_forkers,
    fetch_stargazers,
    fetch_user_metadata,
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
    users = fetch_stargazers(repo)
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
    users = fetch_forkers(repo)
    assert len(users) == 2
    assert users[0]["login"] == "forker1"
    assert users[1]["login"] == "forkerB"
    assert users[0]["forked_at"] == "2023-01-01T00:00:00Z"


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


def test_account_trend_plotting():
    """Test that account trend plotting works correctly with sample data."""
    runner = CliRunner()
    result = runner.invoke(cli, ["account-trend", "testuser", "--line-chart"])
    assert result.exit_code == 0  # Should pass now that we fixed the plotting
    assert "AttributeError: module 'plotext' has no attribute 'plot_date'" not in result.output


def test_account_trend_plotting_fixed():
    """Test that account trend plotting works correctly after fix."""
    runner = CliRunner()
    result = runner.invoke(cli, ["account-trend", "testuser", "--line-chart"])
    assert result.exit_code == 0  # Should pass after fix


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
