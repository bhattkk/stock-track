"""Periodic Postgres backups via pg_dump.

The point is redeploy-ability: when this moves off a laptop to a VPS/Pi,
`docker cp`-ing the latest dump out of the backups volume and `psql`-ing it
back in should be all that's needed to restore watchlists + portfolio — no
manual re-entry.
"""
from __future__ import annotations

import logging
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from config import Settings

log = logging.getLogger("portfolio.backup")


def _pg_dump_url(database_url: str) -> str:
    """Drop SQLAlchemy's '+driver' suffix — pg_dump wants a plain postgresql:// URI."""
    scheme, netloc, path, query, fragment = urlsplit(database_url)
    return urlunsplit((scheme.split("+")[0], netloc, path, query, fragment))


def run_backup(settings: Settings) -> Path | None:
    """Dump the database to a timestamped .sql file, then prune old ones.

    Returns the path written, or None if pg_dump failed. Failures are logged
    and swallowed rather than raised, so a bad backup never takes the
    scheduler (and the rest of the service) down with it.
    """
    backup_dir = Path(settings.backup_dir)
    backup_dir.mkdir(parents=True, exist_ok=True)

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    dest = backup_dir / f"portfolio_{stamp}.sql"

    try:
        with dest.open("wb") as f:
            subprocess.run(
                ["pg_dump", "--no-owner", "--no-privileges", _pg_dump_url(settings.database_url)],
                stdout=f,
                stderr=subprocess.PIPE,
                check=True,
                timeout=120,
            )
    except (subprocess.CalledProcessError, OSError, subprocess.TimeoutExpired):
        log.exception("pg_dump failed")
        dest.unlink(missing_ok=True)
        return None

    log.info("Backup written: %s", dest)
    _prune_old_backups(backup_dir, settings.backup_retention)
    return dest


def _prune_old_backups(backup_dir: Path, keep: int) -> None:
    """Keep only the `keep` most recent dumps (filenames sort chronologically)."""
    dumps = sorted(backup_dir.glob("portfolio_*.sql"), reverse=True)
    for stale in dumps[keep:]:
        stale.unlink(missing_ok=True)
        log.info("Pruned old backup: %s", stale)
