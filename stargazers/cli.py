import os
import sys
import time

import httpx
import pandas as pd
from rich.console import Console
from rich.panel import Panel
from rich.progress import track
from rich.traceback import install
from dotenv import load_dotenv

load_dotenv()

install(show_locals=True)

console = Console()

GITHUB_API = "https://api.github.com"
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
HEADERS = {"Authorization": f"Bearer {GITHUB_TOKEN}"} if GITHUB_TOKEN else {}


def fetch_stargazers(repo: str) -> list:
    console.log(f"[bold blue]Fetching stargazers for:[/] {repo}")
    url = f"{GITHUB_API}/repos/{repo}/stargazers"
    params = {"per_page": 100, "page": 1}
    users = []
    while True:
        console.log(f"Requesting: {url} with params {params}")
        response = httpx.get(
            url,
            headers={**HEADERS, "Accept": "application/vnd.github.v3.star+json"},
            params=params,
        )
        console.log(f"Response status: {response.status_code}")
        if response.status_code == 404:
            console.log(f"[red]Repository '{repo}' not found. Please check the owner/repo name and try again.[/]")
            raise SystemExit(1)
        if response.status_code != 200:
            console.log(
                f"[red]Error fetching stargazers: {response.status_code} - {response.text}[/]"
            )
            sys.exit(1)
        batch = response.json()
        console.log(f"Fetched {len(batch)} stargazers in this batch.")
        if not batch:
            break
        users.extend([{**s["user"], "starred_at": s["starred_at"]} for s in batch])
        params["page"] += 1
        time.sleep(0.5)
    console.log(f"Total stargazers fetched: {len(users)}")
    return users


def fetch_user_metadata(stargazers: list) -> list:
    data = []
    for s in track(stargazers, description="Fetching user metadata"):
        username = s["login"]
        starred_at = s["starred_at"]
        url = f"{GITHUB_API}/users/{username}"
        console.log(f"Fetching metadata for user: {username} ({url})")
        retries = 0
        max_retries = 5
        backoff = 60
        while True:
            r = httpx.get(url, headers=HEADERS)
            console.log(f"User {username} response status: {r.status_code}")
            if r.status_code == 200:
                u = r.json()
                data.append(
                    {
                        "login": u["login"],
                        "name": u.get("name"),
                        "company": u.get("company"),
                        "location": u.get("location"),
                        "email": u.get("email"),
                        "bio": u.get("bio"),
                        "followers": u.get("followers"),
                        "public_repos": u.get("public_repos"),
                        "starred_at": starred_at,
                    }
                )
                time.sleep(0.3)
                break
            if r.status_code == 403 and "rate limit" in r.text.lower():
                if retries < max_retries:
                    console.log(
                        f"[yellow]Rate limit hit for user {username}. "
                        f"Waiting {backoff} seconds before retrying (attempt "
                        f"{retries + 1}/{max_retries})...[/]"
                    )
                    time.sleep(backoff)
                    retries += 1
                    backoff *= 2
                    continue
                console.log(
                    f"[red]Max retries reached for user {username} due to rate limit. Skipping.[/]"
                )
                break
            console.log(
                f"[yellow]Skipping user {username} due to error: {r.status_code} - {r.text}[/]"
            )
            break
    console.log(f"Fetched metadata for {len(data)} users.")
    return data


def summarize_and_save(data: list, repo: str) -> None:
    df = pd.DataFrame(data)
    if "starred_at" in df.columns:
        df = df.sort_values("starred_at", ascending=False)
    output_file = f"{repo.replace('/', '_')}_stargazers.csv"
    console.log(f"Saving DataFrame with {len(df)} rows to {output_file}")
    df.to_csv(output_file, index=False)
    console.print(f"[green]\nSaved {len(df)} users to {output_file}[/]")
    if not df.empty:
        top_locs = df["location"].value_counts().head(10)
        console.print("\nTop Locations:")
        console.print(top_locs)
    else:
        console.log("No user data to summarize.")


def print_help():
    help_text = """
GitHub Stargazer Analyzer

Usage:
  stargazers <owner/repo>

Arguments:
  <owner/repo>   GitHub repository in the format 'owner/repo'

Environment:
  GITHUB_TOKEN   Personal access token for authenticated GitHub API requests
    """
    console.print(
        Panel(
            help_text.strip(),
            title="Help",
            subtitle="GitHub Stargazers",
            expand=False,
        )
    )


def main():
    token = os.getenv("GITHUB_TOKEN")
    if token:
        redacted = token[:4] + "..." + token[-4:]
    else:
        redacted = None
    console.log(f"DEBUG: GITHUB_TOKEN={redacted}")
    console.log(f"Script started with arguments: {sys.argv}")
    if len(sys.argv) != 2 or sys.argv[1] in {"-h", "--help"}:
        print_help()
        sys.exit(0)
    repo = sys.argv[1]
    stargazers = fetch_stargazers(repo)
    console.log(
        f"User logins and starred_at to fetch metadata for: "
        f"{[{'login': s['login'], 'starred_at': s['starred_at']} for s in stargazers]}"
    )
    metadata = fetch_user_metadata(stargazers)
    summarize_and_save(metadata, repo)
