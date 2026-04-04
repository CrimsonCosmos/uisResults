# UIS Results Data

This directory contains the latest scraped UIS athletics results data.

## Files

- `uis-results.json` - Current meet results (automatically updated by GitHub Actions twice daily)

## Purpose

This directory serves as a backup storage for the scraped data before it's deployed to the website repositories. The data is committed here automatically to ensure we have a historical record in version control.

## Storage Management

To prevent GitHub Actions storage issues:
- Old workflow runs are automatically deleted after 7 days
- Weekly cleanup jobs remove old caches and artifacts
- Only the latest results file is kept (overwritten with each update)
