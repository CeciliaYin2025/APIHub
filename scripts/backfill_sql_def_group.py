"""Migration: populate api_hub_sql_definitions.group_name for existing rows.

The group is a new, free-text category that replaced ``permission`` as the
Admin UI's grouping key. Rows created before it existed have an empty group and
would all pile up under "(ungrouped)", so this derives a starting value from the
definition name: the segment before the first underscore (``HR_SPMFG_Emps`` ->
``HR``), or the whole name when it has none (``ResourceReview``).

That is a starting point, not an authority — edit the groups in the Admin UI
afterwards. Only rows whose group is still empty are touched, so re-running
never overwrites a group someone has set.

Safe to run multiple times (idempotent).

Usage:
    python -m scripts.backfill_sql_def_group [--dry-run]
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db.session import SessionLocal
from app.models.sql_definition import SqlDefinitionModel


def derive_group(name: str) -> str:
    head = name.split("_", 1)[0]
    return head or name


def main():
    dry_run = "--dry-run" in sys.argv

    db = SessionLocal()
    try:
        rows = (
            db.query(SqlDefinitionModel)
            .filter((SqlDefinitionModel.group_name == "") | (SqlDefinitionModel.group_name.is_(None)))
            .order_by(SqlDefinitionModel.db_alias, SqlDefinitionModel.name)
            .all()
        )
        if not rows:
            print("No definitions with an empty group_name — nothing to do.")
            return

        for row in rows:
            group = derive_group(row.name)
            print(f"  {row.db_alias}/{row.name} -> group_name='{group}'")
            if not dry_run:
                row.group_name = group

        if dry_run:
            print(f"\n[dry-run] {len(rows)} definition(s) would be updated. Nothing was written.")
        else:
            db.commit()
            print(f"\nUpdated {len(rows)} definition(s).")
            print("Restart the API (or hit the Reload button) so the registry picks up the new groups.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
