# ISAP SQL definitions (seed YAML)

This directory is **bind-mounted** over the image's `app/sql_definitions/` for
the `apihub-api` and Celery services. It is **empty on purpose**.

SQL definitions in production are stored in the PostgreSQL `api_hub_sql_definitions`
table and managed through the Admin UI / API (full CRUD). The YAML files under
`app/sql_definitions/` in the repo are only a **fallback seed** that the registry
loads *when the DB table is empty* — convenient for local dev, but in production
they show up as read-only "phantom" definitions that the CRUD endpoints can't
edit or delete (they only touch the DB), causing 404s.

By mounting this empty directory over that path, the YAML fallback finds nothing,
so production starts with a clean, DB-only definition set and you define
everything via the Admin UI.

If you ever *do* want to seed definitions on a fresh deploy, drop `*.yaml` files
here and either restart, or import them into the DB via
`POST /api/v1/queries/definitions/import-yaml`.
