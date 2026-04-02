# Backup And Restore

## What To Back Up

- SQLite database file referenced by `DATABASE_URL`
- Artifact directory referenced by `ARTIFACTS_DIR`
- Your `.env` file if it contains deployment-specific settings

## Docker Compose Example

With the default compose file, back up:

- `/app/data`
- `/app/artifacts`

## Restore Steps

1. Stop the services.
2. Restore the database file and artifact directory.
3. Restore `.env` if needed.
4. Start the services again with `docker compose up -d`.

The app persists run state chapter-by-chapter, so completed chapters and exported manuscripts can be restored together.
