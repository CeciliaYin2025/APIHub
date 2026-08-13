"""Bootstrap script: create permission tables and a default admin user.

Usage:
    python -m scripts.init_db
"""

import sys
from pathlib import Path

# Ensure project root is on sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.security import get_password_hash
from app.db.session import Base, SessionLocal, engine
from app.models.audit_log import AuditLog  # noqa: F401 — registers model
from app.models.sql_definition import SqlDefinitionModel  # noqa: F401 — registers model
from app.models.user import ApiKey, Permission, Role, User  # noqa: F401 — registers models


def main():
    print("Creating tables...")
    Base.metadata.create_all(bind=engine)

    from sqlalchemy import text

    with engine.begin() as conn:
        conn.execute(
            text("ALTER TABLE api_hub_users ADD COLUMN IF NOT EXISTS is_service_account boolean DEFAULT false NOT NULL")
        )
    print("Ensured api_hub_users.is_service_account column.")

    with engine.begin() as conn:
        conn.execute(
            text("ALTER TABLE api_hub_sql_definitions ADD COLUMN IF NOT EXISTS order_by jsonb DEFAULT '[]'::jsonb")
        )
    print("Ensured api_hub_sql_definitions.order_by column.")

    with engine.begin() as conn:
        conn.execute(
            text(
                "ALTER TABLE api_hub_sql_definitions "
                "ADD COLUMN IF NOT EXISTS group_name varchar(100) DEFAULT '' NOT NULL"
            )
        )
        conn.execute(
            text("CREATE INDEX IF NOT EXISTS ix_sql_def_group_name ON api_hub_sql_definitions (group_name)")
        )
    print("Ensured api_hub_sql_definitions.group_name column.")

    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE api_hub_audit_logs ADD COLUMN IF NOT EXISTS action_type varchar(20)"))
        conn.execute(text("ALTER TABLE api_hub_audit_logs ADD COLUMN IF NOT EXISTS params jsonb"))
        conn.execute(text("ALTER TABLE api_hub_audit_logs ADD COLUMN IF NOT EXISTS result_rows integer"))
        for index_name, columns in (
            ("ix_audit_principal_ts", "principal, ts"),
            ("ix_audit_db_alias_ts", "db_alias, ts"),
            ("ix_audit_action_ts", "action, ts"),
            ("ix_audit_action_type_ts", "action_type, ts"),
        ):
            conn.execute(text(f"CREATE INDEX IF NOT EXISTS {index_name} ON api_hub_audit_logs ({columns})"))
    print("Ensured api_hub_audit_logs report columns and indexes.")

    db = SessionLocal()
    try:
        # Check if admin already exists
        admin = db.query(User).filter(User.username == "admin").first()
        if admin:
            print("Admin user already exists. Skipping seed.")
            return

        # Create default permissions
        perms = [
            Permission(resource="query:*", action="read", description="Read any query definition"),
            Permission(resource="procedure:*", action="execute", description="Execute any procedure/function"),
            Permission(resource="table:*", action="write", description="Write to any table"),
        ]
        for p in perms:
            db.add(p)
        db.flush()

        # Create admin role with all permissions
        admin_role = Role(name="admin", description="Full access")
        admin_role.permissions = perms
        db.add(admin_role)
        db.flush()

        # Create reader role
        reader_role = Role(name="reader", description="Read-only access")
        reader_perm = db.query(Permission).filter(Permission.resource == "query:*", Permission.action == "read").first()
        reader_role.permissions = [reader_perm]
        db.add(reader_role)
        db.flush()

        # Create default admin user
        admin_user = User(
            username="admin",
            hashed_password=get_password_hash("changeme"),
            full_name="System Administrator",
            is_superuser=True,
        )
        admin_user.roles = [admin_role]
        db.add(admin_user)

        db.commit()
        print("Database initialized successfully.")
        print("Default admin user: admin / changeme")
        print("IMPORTANT: Change the admin password immediately!")
    finally:
        db.close()


if __name__ == "__main__":
    main()
