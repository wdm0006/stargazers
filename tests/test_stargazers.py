import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from unittest.mock import patch

import pytest

from stargazers.cli import (
    fetch_forkers,
    fetch_stargazers,
    fetch_user_metadata,
    main_forkers,
    summarize_and_save,
)


# Use httpx_mock for mocking HTTP requests
@pytest.fixture(autouse=True)
def patch_console(monkeypatch):
    # Patch rich.console.Console methods to avoid actual printing during tests
    class DummyConsole:
        def log(self, *args, **kwargs):
            pass

        def print(self, *args, **kwargs):
            pass

    monkeypatch.setattr("stargazers.cli.console", DummyConsole())


def test_fetch_stargazers(httpx_mock):
    repo = "test_owner/test_repo"
    # Mock two pages of stargazers
    stargazers_page1 = [
        {"user": {"login": "user1", "id": 1}, "starred_at": "2023-01-01T00:00:00Z"},
        {"user": {"login": "user2", "id": 2}, "starred_at": "2023-01-02T00:00:00Z"},
    ]
    stargazers_page2 = []
    base_url = f"https://api.github.com/repos/{repo}/stargazers"
    # First page
    httpx_mock.add_response(
        url=f"{base_url}?per_page=100&page=1",
        method="GET",
        match_headers={"Accept": "application/vnd.github.v3.star+json"},
        json=stargazers_page1,
        status_code=200,
    )
    # Second page (empty)
    httpx_mock.add_response(
        url=f"{base_url}?per_page=100&page=2",
        method="GET",
        match_headers={"Accept": "application/vnd.github.v3.star+json"},
        json=stargazers_page2,
        status_code=200,
    )
    users = fetch_stargazers(repo)
    assert len(users) == 2
    assert users[0]["login"] == "user1"
    assert users[1]["login"] == "user2"
    assert users[0]["starred_at"] == "2023-01-01T00:00:00Z"


def test_fetch_user_metadata(httpx_mock):
    stargazers = [
        {"login": "user1", "starred_at": "2023-01-01T00:00:00Z"},
        {"login": "user2", "starred_at": "2023-01-02T00:00:00Z"},
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
    metadata = fetch_user_metadata(stargazers)
    assert len(metadata) == 2
    assert metadata[0]["login"] == "user1"
    assert metadata[1]["login"] == "user2"
    assert metadata[0]["starred_at"] == "2023-01-01T00:00:00Z"


def test_summarize_and_save():
    data = [
        {
            "login": "user1",
            "name": "User One",
            "location": "Earth",
            "starred_at": "2023-01-01T00:00:00Z",
        },
        {
            "login": "user2",
            "name": "User Two",
            "location": "Mars",
            "starred_at": "2023-01-02T00:00:00Z",
        },
    ]
    repo = "test_owner/test_repo"
    # Patch output file location to tmp_path
    with patch("stargazers.cli.pd.DataFrame.to_csv") as mock_to_csv:
        summarize_and_save(data, repo)
        # Check that to_csv was called
        assert mock_to_csv.called


def test_fetch_forkers(httpx_mock):
    repo = "test_owner/test_repo"
    # Mock two pages of forkers
    forkers_page1 = [
        {"owner": {"login": "forker1", "id": 101}, "created_at": "2023-01-01T00:00:00Z"},
        {"owner": {"login": "forker2", "id": 102}, "created_at": "2023-01-02T00:00:00Z"},
    ]
    forkers_page2 = []
    base_url = f"https://api.github.com/repos/{repo}/forks"
    # First page
    httpx_mock.add_response(
        url=f"{base_url}?per_page=100&page=1",
        method="GET",
        json=forkers_page1,
        status_code=200,
    )
    # Second page (empty)
    httpx_mock.add_response(
        url=f"{base_url}?per_page=100&page=2",
        method="GET",
        json=forkers_page2,
        status_code=200,
    )
    users = fetch_forkers(repo)
    assert len(users) == 2
    assert users[0]["login"] == "forker1"
    assert users[1]["login"] == "forker2"
    assert users[0]["forked_at"] == "2023-01-01T00:00:00Z"


def test_main_forkers(monkeypatch, httpx_mock):
    # Patch sys.argv
    import sys

    repo = "test_owner/test_repo"
    monkeypatch.setattr(sys, "argv", ["forkers", repo])
    # Mock forkers API
    forkers_page1 = [
        {"owner": {"login": "forker1", "id": 101}, "created_at": "2023-01-01T00:00:00Z"},
        {"owner": {"login": "forker2", "id": 102}, "created_at": "2023-01-02T00:00:00Z"},
    ]
    forkers_page2 = []
    base_url = f"https://api.github.com/repos/{repo}/forks"
    httpx_mock.add_response(
        url=f"{base_url}?per_page=100&page=1",
        method="GET",
        json=forkers_page1,
        status_code=200,
    )
    httpx_mock.add_response(
        url=f"{base_url}?per_page=100&page=2",
        method="GET",
        json=forkers_page2,
        status_code=200,
    )
    # Mock user metadata API
    user1_data = {
        "login": "forker1",
        "name": "Forker One",
        "company": "TestCo",
        "location": "Earth",
        "email": "forker1@example.com",
        "bio": "Bio1",
        "followers": 10,
        "public_repos": 5,
    }
    user2_data = {
        "login": "forker2",
        "name": "Forker Two",
        "company": None,
        "location": "Mars",
        "email": None,
        "bio": None,
        "followers": 20,
        "public_repos": 8,
    }
    httpx_mock.add_response(
        url="https://api.github.com/users/forker1",
        method="GET",
        json=user1_data,
        status_code=200,
    )
    httpx_mock.add_response(
        url="https://api.github.com/users/forker2",
        method="GET",
        json=user2_data,
        status_code=200,
    )
    # Patch to_csv to avoid file I/O
    from unittest.mock import patch

    with patch("stargazers.cli.pd.DataFrame.to_csv") as mock_to_csv:
        main_forkers()
        assert mock_to_csv.called
