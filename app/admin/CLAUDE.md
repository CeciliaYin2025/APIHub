# Admin UI (`app/admin/`)

Vanilla-JS SPA, **no build step**: `index.html` + one `css/style.css` + one `js/app.js`. `app/main.py` mounts `/admin/css` and `/admin/js` as StaticFiles and serves `index.html` with `{{BASE_PATH}}` substituted from `settings.root_path`, so edits are live on the next hard-refresh — no restart, no rebuild.

Conventions inside `app.js`:

- `navigateTo(page)` maps a sidebar `data-page` key to a `render*()` function and a title; every page renders its HTML into `#contentArea`.
- Shared helpers: `api(method, path, body)` (auth header + error unwrapping), `dbPrefix()` (current `/{alias}` segment), `esc()` for **all** interpolated values, `toast()`, `openModal()/closeModal()`.
- Most sections use modals for create/edit; **SQL Definitions is the exception** — list, detail and create/edit are full pages via `enterSqlDefView()`. Follow the neighbouring section's pattern rather than mixing both in one section.
- Playwright specs in `tests/e2e/` cover the Query Console and raw-JSON copy buttons; run them after non-trivial Admin UI changes.
