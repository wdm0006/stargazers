# Changelog

## 0.4.0 - 2026-10-10

### Added

- Add a `workflows` command that exports GitHub Actions run outcomes and elapsed durations, and summarizes per-workflow success rate and median duration.
- Add a `pulls` command that exports pull request merge outcomes and creation-to-merge durations (elapsed time, not review time).

## 0.3.0 - 2026-09-13

### Added

- Add a `releases` command that exports release cadence, asset counts, and download totals.
- Add a `commits` command that exports commit history and summarizes cadence, merges, and authorship.
- Add an `overview` command that exports an account's repository portfolio without extra per-repository API calls.
- Export a per-repository, per-day traffic CSV alongside the existing traffic totals.

### Fixed

- Match repository owners case-insensitively when discovering repositories for an account.
- Warn after repository exports when pagination was incomplete, and identify repositories with unavailable clone or referrer data.
- Preserve the documented daily traffic CSV column order and blank clone values when clone data is unavailable.
