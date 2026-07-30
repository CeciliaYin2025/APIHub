"""Delete audit rows older than the retention window.

Usage:
    python -m scripts.purge_audit_logs [--days N] [--batch-size N] [--dry-run]

Retention is the only thing bounding api_hub_audit_logs growth: params are
stored untruncated, so a batch write payload can be megabytes per row.
Run this from cron / a scheduled task; it is deliberately not a Celery beat
job so it never competes with the write/procedure queues.
"""

import argparse
import sys
from datetime import datetime, timedelta
from pathlib import Path

# Ensure project root is on sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.config import settings
from app.db.session import SessionLocal
from app.models.audit_log import AuditLog


def purge_audit_logs(db, days: int, batch_size: int = 10000, dry_run: bool = False) -> int:
    """Delete api_hub_audit_logs rows older than *days*.

    Deletes in committed batches so a large backlog never holds one long
    transaction (and its locks) open against a table the API writes to on
    every request. Returns the number of rows deleted, or under *dry_run*
    the number that would be deleted.
    """
    cutoff = datetime.now() - timedelta(days=days)
    if dry_run:
        return db.query(AuditLog).filter(AuditLog.ts < cutoff).count()

    deleted = 0
    while True:
        ids = [row[0] for row in db.query(AuditLog.id).filter(AuditLog.ts < cutoff).limit(batch_size).all()]
        if not ids:
            return deleted
        db.query(AuditLog).filter(AuditLog.id.in_(ids)).delete(synchronize_session=False)
        db.commit()
        deleted += len(ids)


def main():
    parser = argparse.ArgumentParser(description="Purge old api_hub_audit_logs rows")
    parser.add_argument("--days", type=int, default=settings.audit_retention_days,
                        help="Retention window in days (default: AUDIT_RETENTION_DAYS)")
    parser.add_argument("--batch-size", type=int, default=10000, help="Rows deleted per transaction")
    parser.add_argument("--dry-run", action="store_true", help="Report the row count without deleting")
    args = parser.parse_args()

    db = SessionLocal()
    try:
        count = purge_audit_logs(db, days=args.days, batch_size=args.batch_size, dry_run=args.dry_run)
    finally:
        db.close()

    verb = "would delete" if args.dry_run else "deleted"
    print(f"Retention {args.days} days: {verb} {count} audit row(s).")


if __name__ == "__main__":
    main()
