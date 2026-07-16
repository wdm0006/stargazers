import os
import time

import click
import httpx
import pandas as pd
import plotext as plt
from dotenv import load_dotenv
from rich.console import Console
from rich.progress import track
from rich.traceback import install

load_dotenv()

install(show_locals=False)

console = Console()

GITHUB_API = "https://api.github.com"
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
HEADERS = {"Authorization": f"Bearer {GITHUB_TOKEN}"} if GITHUB_TOKEN else {}
DEFAULT_HEADERS = {"Accept": "application/vnd.github.v3+json"}
STAR_HEADERS = {"Accept": "application/vnd.github.v3.star+json"}

# Maximum number of rate-limit retries for a single page of a paginated fetch.
MAX_RATE_LIMIT_RETRIES = 5


def _handle_api_error(response: httpx.Response, context_message: str):
    """Handles common API errors."""
    if response.status_code == 404:
        console.log(f"[red]{context_message} not found. Please check the input and try again.[/]")
        raise SystemExit(1)
    if response.status_code == 403 and "rate limit" in response.text.lower():
        console.log(f"[yellow]Rate limit hit. Headers: {response.headers}[/]")
        # Basic wait, more sophisticated backoff might be needed for heavy use
        wait_time = 60
        reset_time = response.headers.get("X-RateLimit-Reset")
        if reset_time:
            wait_time = max(1, int(reset_time) - int(time.time()))
            console.log(f"[yellow]Rate limit reset time: {reset_time}. Waiting for {wait_time} seconds.[/]")
        else:
            console.log(f"[yellow]No X-RateLimit-Reset header. Waiting for {wait_time} seconds as a fallback.[/]")

        with console.status(f"[yellow]Rate limit hit. Waiting for {wait_time}s...[/]", spinner="dots"):
            time.sleep(wait_time)
        console.log("[yellow]Resuming...[/]")
        return "retry"  # Signal to retry the request
    if response.status_code != 200:
        console.log(f"[red]Error: {response.status_code} - {response.text}. {context_message}[/]")
        raise SystemExit(1)
    return None


def fetch_user_repos(username: str) -> list[str]:
    """Fetches all repositories for a given user where they are the owner."""
    console.log(f"[bold blue]Fetching repositories for user:[/] {username}")
    repos = []
    url = f"{GITHUB_API}/users/{username}/repos"
    params = {"type": "owner", "sort": "full_name", "per_page": 100, "page": 1}
    rate_limit_retries = 0

    while True:
        console.log(f"Requesting: {url} with params {params}")
        try:
            response = httpx.get(url, headers={**HEADERS, **DEFAULT_HEADERS}, params=params)
        except httpx.RequestError as e:
            console.log(f"[red]Request failed: {e}[/]")
            raise SystemExit(1) from e

        error_action = _handle_api_error(response, f"fetching repos for user {username}")
        if error_action == "retry":
            rate_limit_retries += 1
            if rate_limit_retries > MAX_RATE_LIMIT_RETRIES:
                console.log(
                    f"[bold red]Giving up fetching repos for {username} — still rate limited after "
                    f"{MAX_RATE_LIMIT_RETRIES} retries on page {params['page']}.[/]"
                )
                raise SystemExit(1)
            continue  # Retry the current page request
        rate_limit_retries = 0

        batch = response.json()
        if not batch:
            break

        repos.extend([repo["full_name"] for repo in batch if repo["owner"]["login"] == username])
        console.log(f"Fetched {len(batch)} repos in this batch. Total relevant repos so far: {len(repos)}")

        if "next" in response.links:
            params["page"] += 1
        else:
            break
        time.sleep(0.2)  # Brief pause

    console.log(f"Total repositories fetched for {username}: {len(repos)}")
    return repos


def fetch_stargazers(repo: str) -> tuple[list, bool]:
    """Fetch stargazers for a repo.

    Returns a ``(events, complete)`` tuple. ``complete`` is ``False`` when
    pagination was cut short by a network error or by exhausting
    ``MAX_RATE_LIMIT_RETRIES``, so the events are partial.
    """
    console.log(f"[bold blue]Fetching stargazers for:[/] {repo}")
    url = f"{GITHUB_API}/repos/{repo}/stargazers"
    params = {"per_page": 100, "page": 1}
    users_with_starred_at = []
    rate_limit_retries = 0
    while True:
        console.log(f"Requesting: {url} with params {params}")
        try:
            response = httpx.get(url, headers={**HEADERS, **STAR_HEADERS}, params=params)
        except httpx.RequestError as e:
            console.log(
                f"[bold red]WARNING: incomplete data for {repo} — results are partial "
                f"(network error during pagination: {e})[/]"
            )
            # Return what was fetched so far, flagged as incomplete.
            return users_with_starred_at, False

        error_action = _handle_api_error(response, f"fetching stargazers for repo {repo}")
        if error_action == "retry":
            rate_limit_retries += 1
            if rate_limit_retries > MAX_RATE_LIMIT_RETRIES:
                console.log(
                    f"[bold red]WARNING: incomplete data for {repo} — results are partial "
                    f"(still rate limited after {MAX_RATE_LIMIT_RETRIES} retries)[/]"
                )
                return users_with_starred_at, False
            continue
        rate_limit_retries = 0

        batch = response.json()
        console.log(f"Fetched {len(batch)} stargazers in this batch.")
        if not batch:
            break

        # batch is a list of {'starred_at': '...', 'user': {...}}
        users_with_starred_at.extend(
            [{"login": s["user"]["login"], "starred_at": s["starred_at"], "user_details": s["user"]} for s in batch]
        )

        if "next" in response.links:
            params["page"] += 1
        else:
            break
        time.sleep(0.2)  # Brief pause
    console.log(f"Total stargazers fetched for {repo}: {len(users_with_starred_at)}")
    return users_with_starred_at, True


def fetch_forkers(repo: str) -> tuple[list, bool]:
    """Fetch forkers for a repo.

    Returns a ``(events, complete)`` tuple. ``complete`` is ``False`` when
    pagination was cut short by a network error or by exhausting
    ``MAX_RATE_LIMIT_RETRIES``, so the events are partial.
    """
    console.log(f"[bold blue]Fetching forkers for:[/] {repo}")
    url = f"{GITHUB_API}/repos/{repo}/forks"
    params = {"per_page": 100, "page": 1}
    fork_details = []
    rate_limit_retries = 0
    while True:
        console.log(f"Requesting: {url} with params {params}")
        try:
            response = httpx.get(url, headers={**HEADERS, **DEFAULT_HEADERS}, params=params)
        except httpx.RequestError as e:
            console.log(
                f"[bold red]WARNING: incomplete data for {repo} — results are partial "
                f"(network error during pagination: {e})[/]"
            )
            # Return what was fetched so far, flagged as incomplete.
            return fork_details, False

        error_action = _handle_api_error(response, f"fetching forkers for repo {repo}")
        if error_action == "retry":
            rate_limit_retries += 1
            if rate_limit_retries > MAX_RATE_LIMIT_RETRIES:
                console.log(
                    f"[bold red]WARNING: incomplete data for {repo} — results are partial "
                    f"(still rate limited after {MAX_RATE_LIMIT_RETRIES} retries)[/]"
                )
                return fork_details, False
            continue
        rate_limit_retries = 0

        batch = response.json()
        console.log(f"Fetched {len(batch)} forkers in this batch.")
        if not batch:
            break

        # batch is a list of full fork objects, owner is in fork['owner']
        fork_details.extend(
            [
                {"login": fork["owner"]["login"], "forked_at": fork["created_at"], "user_details": fork["owner"]}
                for fork in batch
            ]
        )

        if "next" in response.links:
            params["page"] += 1
        else:
            break
        time.sleep(0.2)  # Brief pause
    console.log(f"Total forkers fetched for {repo}: {len(fork_details)}")
    return fork_details, True


def fetch_traffic_views(repo: str) -> dict | None:
    """Fetches traffic view data for a repository (last 14 days). Requires push access."""
    url = f"{GITHUB_API}/repos/{repo}/traffic/views"
    try:
        response = httpx.get(url, headers={**HEADERS, **DEFAULT_HEADERS})
    except httpx.RequestError as e:
        console.log(f"[red]Request failed fetching views for {repo}: {e}[/]")
        return None

    if response.status_code == 403:
        console.log(f"[yellow]No push access to {repo}, skipping traffic data.[/]")
        return None

    error_action = _handle_api_error(response, f"fetching traffic views for {repo}")
    if error_action == "retry":
        try:
            response = httpx.get(url, headers={**HEADERS, **DEFAULT_HEADERS})
        except httpx.RequestError:
            return None
        if response.status_code != 200:
            return None

    return response.json()


def fetch_traffic_clones(repo: str) -> dict | None:
    """Fetches traffic clone data for a repository (last 14 days). Requires push access."""
    url = f"{GITHUB_API}/repos/{repo}/traffic/clones"
    try:
        response = httpx.get(url, headers={**HEADERS, **DEFAULT_HEADERS})
    except httpx.RequestError as e:
        console.log(f"[red]Request failed fetching clones for {repo}: {e}[/]")
        return None

    if response.status_code == 403:
        console.log(f"[yellow]No push access to {repo}, skipping clone data.[/]")
        return None

    error_action = _handle_api_error(response, f"fetching traffic clones for {repo}")
    if error_action == "retry":
        try:
            response = httpx.get(url, headers={**HEADERS, **DEFAULT_HEADERS})
        except httpx.RequestError:
            return None
        if response.status_code != 200:
            return None

    return response.json()


def fetch_traffic_referrers(repo: str) -> list | None:
    """Fetches top referral sources for a repository (last 14 days). Requires push access."""
    url = f"{GITHUB_API}/repos/{repo}/traffic/popular/referrers"
    try:
        response = httpx.get(url, headers={**HEADERS, **DEFAULT_HEADERS})
    except httpx.RequestError as e:
        console.log(f"[red]Request failed fetching referrers for {repo}: {e}[/]")
        return None

    if response.status_code == 403:
        console.log(f"[yellow]No push access to {repo}, skipping referrer data.[/]")
        return None

    error_action = _handle_api_error(response, f"fetching traffic referrers for {repo}")
    if error_action == "retry":
        try:
            response = httpx.get(url, headers={**HEADERS, **DEFAULT_HEADERS})
        except httpx.RequestError:
            return None
        if response.status_code != 200:
            return None

    return response.json()


def fetch_user_metadata(users_info: list, timestamp_key: str = "starred_at") -> list:
    """
    Fetches detailed metadata for a list of users.
    users_info is a list of dicts, each must have 'login' and the timestamp_key.
    """
    data = []
    if not users_info:
        console.log("No users to fetch metadata for.")
        return data

    for user_event in track(users_info, description="Fetching user metadata"):
        username = user_event["login"]
        timestamp_value = user_event[timestamp_key]

        # If full user_details are already prefetched (e.g. from stargazers/forks endpoint)
        if user_event.get("user_details"):
            u = user_event["user_details"]
            # Check if we have enough details, otherwise fetch more
            # This is a simple check; ideally, we'd know which fields are essential
            if all(k in u for k in ["name", "company", "location", "email", "bio", "followers", "public_repos"]):
                console.log(f"Using prefetched metadata for user: {username}")
                details = {
                    "login": u.get("login"),  # login is primary key
                    "name": u.get("name"),
                    "company": u.get("company"),
                    "location": u.get("location"),
                    "email": u.get("email"),
                    "bio": u.get("bio"),
                    "followers": u.get("followers"),
                    "public_repos": u.get("public_repos"),
                    timestamp_key: timestamp_value,  # Add the original timestamp
                }
                if "repo" in user_event:  # Preserve repo if it's there
                    details["repo"] = user_event["repo"]
                data.append(details)
                continue  # Skip dedicated API call for this user

        # Fallback to fetching full metadata if not sufficiently prefetched
        user_api_url = f"{GITHUB_API}/users/{username}"
        console.log(f"Fetching full metadata for user: {username} ({user_api_url})")

        retries = 0
        max_retries = 3  # Reduced max_retries for individual user metadata

        while retries < max_retries:
            try:
                r = httpx.get(user_api_url, headers=HEADERS)
            except httpx.RequestError as e:
                console.log(f"[red]Request failed for user {username}: {e}[/]")
                break  # Skip this user on request failure

            error_action = _handle_api_error(r, f"fetching metadata for user {username}")
            if error_action == "retry":
                # For user metadata, a simpler retry without complex backoff here,
                # as the main rate limit handling is at the list level.
                console.log(f"[yellow]Rate limit hit for user {username}. Waiting 60s and retrying...[/]")
                time.sleep(60)
                retries += 1
                continue

            if r.status_code == 200:
                u = r.json()
                details = {
                    "login": u["login"],
                    "name": u.get("name"),
                    "company": u.get("company"),
                    "location": u.get("location"),
                    "email": u.get("email"),
                    "bio": u.get("bio"),
                    "followers": u.get("followers"),
                    "public_repos": u.get("public_repos"),
                    timestamp_key: timestamp_value,
                }
                if "repo" in user_event:  # Preserve repo if it's there
                    details["repo"] = user_event["repo"]
                data.append(details)
                time.sleep(0.1)  # Brief pause
                break
            # Non-200, non-403 error handled by _handle_api_error or caught here
            console.log(f"[yellow]Skipping user {username} due to error: {r.status_code} - {r.text}[/]")
            break  # Skip this user
        else:  # Loop exited due to max_retries
            console.log(f"[red]Max retries reached for user {username}. Skipping.[/]")

    console.log(f"Fetched metadata for {len(data)} users.")
    return data


def summarize_and_save(data: list, base_name: str, output_file_suffix: str, timestamp_key: str) -> None:
    if not data:
        console.log("[yellow]No data to summarize or save.[/]")
        return

    df = pd.DataFrame(data)
    if timestamp_key in df.columns:
        df[timestamp_key] = pd.to_datetime(df[timestamp_key])
        df = df.sort_values(timestamp_key, ascending=False)

    output_file = f"{base_name.replace('/', '_')}_{output_file_suffix}.csv"
    console.log(f"Saving DataFrame with {len(df)} rows to {output_file}")
    df.to_csv(output_file, index=False)
    console.print(f"[green]\nSaved {len(df)} items to {output_file}[/]")

    if not df.empty and "location" in df.columns:  # Stargazer/forker specific summary
        top_locs = df["location"].value_counts().head(10)
        if not top_locs.empty:
            console.print("\nTop Locations:")
            console.print(top_locs)
    elif not df.empty and "total_new_stars_on_day" in df.columns:  # Account trend specific summary
        console.print("\nStar Trend Summary:")
        console.print(f"Date range: {df['star_date'].min()} to {df['star_date'].max()}")
        console.print(f"Total new stars in period: {df['total_new_stars_on_day'].sum()}")
        console.print(
            f"Final cumulative stars: {df['total_cumulative_stars_up_to_day'].iloc[-1] if not df.empty else 0}"
        )

        # Print per-repository summaries
        repo_columns = [col for col in df.columns if col.endswith("_new_stars")]
        if repo_columns:
            console.print("\nPer-Repository Summary:")
            for col in repo_columns:
                repo_name = col[:-10].replace("_", "/")  # Remove '_new_stars' and restore '/'
                cumul_col = f"{col[:-10]}_cumulative_stars"
                final_stars = df[cumul_col].iloc[0] if not df.empty else 0
                console.print(f"{repo_name}: {final_stars} total stars")


def plot_account_trend(df: pd.DataFrame, title: str) -> None:
    """Helper function to plot account trend data from a DataFrame."""
    if df.empty:
        console.log("[yellow]No data available to display the line chart.[/yellow]")
        return

    console.print()  # Add some space before the chart

    # Convert dates to numerical values for plotting (days since first star)
    first_date = df["star_date"].iloc[0]
    x_values = [(d - first_date).days for d in df["star_date"]]
    y_values = df["cumulative_stars_up_to_day"].tolist()

    plt.clc()
    plt.title(title or "Cumulative Stars Over Time")
    plt.xlabel("Days since first star")
    plt.ylabel("Cumulative Stars")
    plt.scatter(x_values, y_values, marker="braille")
    plt.show()
    console.print()  # Add a newline after the plot for better separation


@click.group()
@click.version_option(package_name="stargazers")  # Assumes 'name' in pyproject.toml is 'stargazers'
def cli():
    """
    A CLI tool to fetch, analyze, and summarize stargazers, forkers,
    or star trends for GitHub repositories and users.
    """
    token = os.getenv("GITHUB_TOKEN")
    if not token:
        console.log("[yellow]Warning: GITHUB_TOKEN not set. You may hit rate limits quickly.[/]")


@cli.command("repos")
@click.argument("repositories", nargs=-1, required=True)
@click.pass_context
def stargazers_repos_command(ctx, repositories: tuple[str]):
    """Fetches and analyzes STARGÀZERS for one or more repositories."""
    console.log(f"Command: 'repos', Args: {repositories}")
    all_metadata = []
    for repo_full_name in repositories:
        if "/" not in repo_full_name:
            console.log(f"[red]Invalid repository format: '{repo_full_name}'. Must be 'owner/repo'.[/]")
            continue

        stargazer_events, _complete = fetch_stargazers(
            repo_full_name
        )  # List of {'login': ..., 'starred_at': ..., 'user_details': ...}

        # Add repo_full_name to each stargazer event before fetching metadata
        for sg_event in stargazer_events:
            sg_event["repo"] = repo_full_name

        metadata = fetch_user_metadata(stargazer_events, timestamp_key="starred_at")
        all_metadata.extend(metadata)

    if not all_metadata:
        console.log("[yellow]No stargazer data fetched for any repository.[/]")
        return

    if len(repositories) == 1 and "/" in repositories[0]:
        base_output_name = repositories[0]
    else:
        base_output_name = "all_repos"  # For multiple repos or if a single one was invalid

    summarize_and_save(all_metadata, base_output_name, "stargazers", timestamp_key="starred_at")


@cli.command("forkers")
@click.argument("repositories", nargs=-1, required=True)
@click.pass_context
def forkers_command(ctx, repositories: tuple[str]):
    """Fetches and analyzes FORKERS for one or more repositories."""
    console.log(f"Command: 'forkers', Args: {repositories}")
    all_metadata = []
    for repo_full_name in repositories:
        if "/" not in repo_full_name:
            console.log(f"[red]Invalid repository format: '{repo_full_name}'. Must be 'owner/repo'.[/]")
            continue

        # List of {'login': ..., 'forked_at': ..., 'user_details': ...}
        forker_events, _complete = fetch_forkers(repo_full_name)

        for fk_event in forker_events:
            fk_event["repo"] = repo_full_name

        metadata = fetch_user_metadata(forker_events, timestamp_key="forked_at")
        all_metadata.extend(metadata)

    if not all_metadata:
        console.log("[yellow]No forker data fetched for any repository.[/]")
        return

    if len(repositories) == 1 and "/" in repositories[0]:
        base_output_name = repositories[0]
    else:
        base_output_name = "all_repos"

    summarize_and_save(all_metadata, base_output_name, "forkers", timestamp_key="forked_at")


@cli.command("account-trend")
@click.argument("username")
@click.option(
    "--exclude-repo",
    "exclude_repos",
    multiple=True,
    help="Repositories to exclude (e.g., owner/repo). Can be used multiple times.",
)
@click.option(
    "--include-repo",
    "include_repos",
    multiple=True,
    help="Additional repositories to include (format: owner/repo). Can be used multiple times.",
)
@click.option("--line-chart", is_flag=True, help="Display a line chart of cumulative stars over time in the terminal.")
@click.pass_context
def account_trend_command(ctx, username: str, exclude_repos: tuple[str], include_repos: tuple[str], line_chart: bool):
    """
    Analyzes star trends over time for a user's owned repositories and any additionally included ones.
    Fetches star events across all specified repositories (owned by USERNAME + included, minus excluded),
    aggregates them by day, and calculates cumulative stars.
    """
    console.log(
        "Command: 'account-trend', "
        f"User: {username}, "
        f"Include Repos: {include_repos}, "
        f"Exclude Repos: {exclude_repos}, "
        f"Line Chart: {line_chart}"
    )

    user_owned_repos = fetch_user_repos(username)
    console.log(f"Found {len(user_owned_repos)} repositories owned by {username}.")

    # Combine owned and explicitly included repos
    candidate_repos = list(user_owned_repos)
    if include_repos:
        console.log(f"Additionally including {len(include_repos)} repositories: {include_repos}")
        candidate_repos.extend(list(include_repos))

    # Deduplicate
    unique_candidate_repos = sorted(list(set(candidate_repos)))
    console.log(f"Total unique candidate repositories (owned + included): {len(unique_candidate_repos)}")

    # Filter repositories based on exclusions
    if exclude_repos:
        console.log(f"Excluding: {exclude_repos}")
        repos_to_process = [repo for repo in unique_candidate_repos if repo not in exclude_repos]
        console.log(f"Processing {len(repos_to_process)} repositories after exclusions.")
    else:
        repos_to_process = unique_candidate_repos
        console.log(f"Processing all {len(repos_to_process)} unique candidate repositories.")

    if not repos_to_process:
        console.log(f"[yellow]No repositories left to process for user {username} after inclusions/exclusions.[/]")
        return

    all_star_events = []
    incomplete_repos = []
    for repo_name in track(repos_to_process, description=f"Fetching stars for {username}'s repos"):
        console.log(f"Fetching stars for repository: {repo_name}")
        stargazer_events_for_repo, complete = fetch_stargazers(repo_name)
        if not complete:
            incomplete_repos.append(repo_name)
        for star_event in stargazer_events_for_repo:
            all_star_events.append({"repo_name": repo_name, "starred_at": star_event["starred_at"]})

    if not all_star_events:
        console.log(f"[yellow]No star events found across processed repositories for {username}.[/]")
        return

    console.log(f"Total star events collected: {len(all_star_events)}")

    df = pd.DataFrame(all_star_events)
    df["starred_at"] = pd.to_datetime(df["starred_at"])
    df["star_date"] = df["starred_at"].dt.date

    # Calculate per-repository daily stars
    per_repo_daily = df.groupby(["repo_name", "star_date"]).size().reset_index(name="new_stars_on_day")
    per_repo_daily = per_repo_daily.sort_values(["repo_name", "star_date"])

    # Calculate per-repository cumulative stars
    per_repo_daily["cumulative_stars_up_to_day"] = per_repo_daily.groupby("repo_name")["new_stars_on_day"].cumsum()

    # Calculate overall daily stars (across all repos)
    daily_stars = df.groupby("star_date").size().reset_index(name="total_new_stars_on_day")
    daily_stars = daily_stars.sort_values("star_date")

    # Ensure all dates are present for cumulative sum, fill missing with 0 new stars
    if not daily_stars.empty:
        daily_stars["star_date"] = pd.to_datetime(daily_stars["star_date"])
        idx = pd.date_range(daily_stars["star_date"].min(), daily_stars["star_date"].max(), freq="D")
        daily_stars = daily_stars.set_index("star_date").reindex(idx, fill_value=0).reset_index()
        daily_stars.rename(columns={"index": "star_date"}, inplace=True)
        daily_stars["star_date"] = daily_stars["star_date"].dt.date
    else:
        if "star_date" not in daily_stars.columns:
            daily_stars["star_date"] = pd.Series(dtype="object")
        if "total_new_stars_on_day" not in daily_stars.columns:
            daily_stars["total_new_stars_on_day"] = pd.Series(dtype="int")

    daily_stars["total_cumulative_stars_up_to_day"] = daily_stars["total_new_stars_on_day"].cumsum()

    # Merge per-repository data with overall daily stats
    final_df = daily_stars.copy()
    for repo in repos_to_process:
        repo_data = per_repo_daily[per_repo_daily["repo_name"] == repo]
        if not repo_data.empty:
            # Create new columns for this repo
            repo_col_prefix = repo.replace("/", "_")
            final_df[f"{repo_col_prefix}_new_stars"] = 0  # Initialize with zeros

            # Create a temporary DataFrame with all dates and merge with repo data
            temp_df = pd.DataFrame(final_df["star_date"].unique(), columns=["star_date"])
            temp_df = temp_df.merge(repo_data[["star_date", "cumulative_stars_up_to_day"]], on="star_date", how="left")
            temp_df = temp_df.sort_values("star_date")
            temp_df["cumulative_stars_up_to_day"] = temp_df["cumulative_stars_up_to_day"].ffill()
            temp_df["cumulative_stars_up_to_day"] = temp_df["cumulative_stars_up_to_day"].fillna(0).astype(int)

            # Update final_df with new stars and cumulative stars
            for _, row in repo_data.iterrows():
                mask = final_df["star_date"] == row["star_date"]
                final_df.loc[mask, f"{repo_col_prefix}_new_stars"] = row["new_stars_on_day"]

            # Update cumulative stars from the temporary DataFrame
            final_df = final_df.merge(
                temp_df.rename(columns={"cumulative_stars_up_to_day": f"{repo_col_prefix}_cumulative_stars"}),
                on="star_date",
                how="left",
            )

    if line_chart and not final_df.empty:
        plot_account_trend(
            final_df.rename(columns={"total_cumulative_stars_up_to_day": "cumulative_stars_up_to_day"}),
            f"Cumulative Stars Over Time for {username}",
        )
    elif line_chart:
        console.log("[yellow]No data available to display the line chart.[/yellow]")

    if incomplete_repos:
        console.log(
            "[bold red]WARNING: star data was incomplete for "
            f"{len(incomplete_repos)} repo(s) ({', '.join(incomplete_repos)}) — "
            "the saved trend may UNDERCOUNT stars. Re-run to get complete data.[/]"
        )

    output_base_name = username
    summarize_and_save(final_df.to_dict("records"), output_base_name, "account_stars_by_day", timestamp_key="star_date")


@cli.command("traffic")
@click.argument("username")
@click.option(
    "--exclude-repo",
    "exclude_repos",
    multiple=True,
    help="Repositories to exclude (e.g., owner/repo). Can be used multiple times.",
)
@click.option(
    "--include-repo",
    "include_repos",
    multiple=True,
    help="Additional repositories to include (format: owner/repo). Can be used multiple times.",
)
@click.pass_context
def traffic_command(ctx, username: str, exclude_repos: tuple[str], include_repos: tuple[str]):
    """
    Aggregates GitHub traffic data (views, clones, referrers) across all repositories
    owned by USERNAME. Shows totals, top repos by views, and aggregated referrer data.

    Note: Requires a GITHUB_TOKEN with push access to the repos. Traffic data covers the last 14 days only.
    """
    console.log(f"Command: 'traffic', User: {username}, Include Repos: {include_repos}, Exclude Repos: {exclude_repos}")

    user_owned_repos = fetch_user_repos(username)
    console.log(f"Found {len(user_owned_repos)} repositories owned by {username}.")

    candidate_repos = list(user_owned_repos)
    if include_repos:
        console.log(f"Additionally including {len(include_repos)} repositories: {include_repos}")
        candidate_repos.extend(list(include_repos))

    unique_candidate_repos = sorted(list(set(candidate_repos)))

    if exclude_repos:
        console.log(f"Excluding: {exclude_repos}")
        repos_to_process = [repo for repo in unique_candidate_repos if repo not in exclude_repos]
    else:
        repos_to_process = unique_candidate_repos

    if not repos_to_process:
        console.log(f"[yellow]No repositories to process for user {username}.[/]")
        return

    # Collect traffic data across all repos
    repo_views = []
    repo_clones = []
    all_referrers = {}  # referrer -> {count, uniques}
    skipped = 0

    for repo_name in track(repos_to_process, description=f"Fetching traffic for {username}'s repos"):
        views = fetch_traffic_views(repo_name)
        if views is None:
            skipped += 1
            continue

        clones = fetch_traffic_clones(repo_name)
        referrers = fetch_traffic_referrers(repo_name)
        time.sleep(0.2)

        repo_views.append(
            {
                "repo": repo_name,
                "views": views.get("count", 0),
                "unique_views": views.get("uniques", 0),
            }
        )
        repo_clones.append(
            {
                "repo": repo_name,
                "clones": clones.get("count", 0) if clones else 0,
                "unique_clones": clones.get("uniques", 0) if clones else 0,
            }
        )

        if referrers:
            for ref in referrers:
                name = ref["referrer"]
                if name not in all_referrers:
                    all_referrers[name] = {"count": 0, "uniques": 0}
                all_referrers[name]["count"] += ref["count"]
                all_referrers[name]["uniques"] += ref["uniques"]

    if not repo_views:
        console.log("[yellow]No traffic data retrieved. Ensure your token has push access to the repos.[/]")
        return

    # Build DataFrames
    views_df = pd.DataFrame(repo_views).sort_values("views", ascending=False)
    clones_df = pd.DataFrame(repo_clones).sort_values("clones", ascending=False)

    total_views = views_df["views"].sum()
    total_unique_views = views_df["unique_views"].sum()
    total_clones = clones_df["clones"].sum()
    total_unique_clones = clones_df["unique_clones"].sum()

    # Print summary
    console.print("\n[bold]Traffic Summary (last 14 days)[/bold]")
    console.print(f"Repos analyzed: {len(repo_views)} (skipped {skipped} due to access)")
    console.print(f"\n[bold]Total Views:[/bold] {total_views:,} ({total_unique_views:,} unique)")
    console.print(f"[bold]Total Clones:[/bold] {total_clones:,} ({total_unique_clones:,} unique)")

    # Top repos by views
    top_n = min(10, len(views_df))
    console.print(f"\n[bold]Top {top_n} Repos by Views:[/bold]")
    for _, row in views_df.head(top_n).iterrows():
        clone_row = clones_df[clones_df["repo"] == row["repo"]].iloc[0]
        console.print(
            f"  {row['repo']}: {row['views']:,} views ({row['unique_views']:,} unique), "
            f"{clone_row['clones']:,} clones ({clone_row['unique_clones']:,} unique)"
        )

    # Aggregated referrers
    if all_referrers:
        ref_df = pd.DataFrame(
            [{"referrer": k, "count": v["count"], "uniques": v["uniques"]} for k, v in all_referrers.items()]
        ).sort_values("count", ascending=False)

        console.print("\n[bold]Top Referrers (aggregated across all repos):[/bold]")
        for _, row in ref_df.head(15).iterrows():
            console.print(f"  {row['referrer']}: {row['count']:,} ({row['uniques']:,} unique)")

    # Save to CSV
    combined_df = views_df.merge(clones_df, on="repo")
    output_file = f"{username}_traffic.csv"
    combined_df.to_csv(output_file, index=False)
    console.print(f"\n[green]Saved per-repo traffic data to {output_file}[/]")

    if all_referrers:
        ref_output = f"{username}_referrers.csv"
        ref_df.to_csv(ref_output, index=False)
        console.print(f"[green]Saved aggregated referrer data to {ref_output}[/]")


@cli.command("plot")
@click.option("--file", required=True, type=click.Path(exists=True), help="Path to the CSV file to plot.")
@click.option(
    "--type", "plot_type", required=True, type=click.Choice(["account-trend"]), help="Type of plot to generate."
)
@click.option("--title", help="Optional title for the plot. If not provided, will be inferred from the filename.")
def plot_command(file: str, plot_type: str, title: str | None):
    """Generate plots from existing CSV files without re-querying the GitHub API."""
    console.log(f"Command: 'plot', File: {file}, Type: {plot_type}, Title: {title}")

    try:
        # Specify data types for numeric columns
        dtype_map = {"total_new_stars_on_day": "int", "total_cumulative_stars_up_to_day": "int"}
        df = pd.read_csv(file, dtype=dtype_map)
    except Exception as e:
        click.echo(f"Error reading CSV file: {e}", err=True)
        raise SystemExit(1) from e

    if plot_type == "account-trend":
        required_columns = {"star_date", "total_new_stars_on_day", "total_cumulative_stars_up_to_day"}
        if not all(col in df.columns for col in required_columns):
            click.echo(f"CSV file must contain columns: {', '.join(required_columns)}", err=True)
            raise SystemExit(1)

        # Convert star_date to datetime
        try:
            df["star_date"] = pd.to_datetime(df["star_date"])
            df = df.sort_values("star_date")
        except Exception as e:
            click.echo(f"Error converting dates: {e}", err=True)
            raise SystemExit(1) from e

        # If no title provided, try to extract from filename
        if not title:
            base_name = os.path.splitext(os.path.basename(file))[0]
            if base_name.endswith("_account_stars_by_day"):
                title = f"Cumulative Stars Over Time for {base_name.replace('_account_stars_by_day', '')}"
            else:
                title = "Cumulative Stars Over Time"

        plot_account_trend(df.rename(columns={"total_cumulative_stars_up_to_day": "cumulative_stars_up_to_day"}), title)
    else:
        click.echo(f"Plot type {plot_type} not implemented yet.", err=True)
        raise SystemExit(1)


if __name__ == "__main__":
    cli()
