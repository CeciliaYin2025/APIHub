# Admin UI (`app/admin/`)

Vanilla-JS SPA, **no build step**: `index.html` + one `css/style.css` + two JS files. `app/main.py` mounts `/admin/css` and `/admin/js` as StaticFiles and serves `index.html` with `{{BASE_PATH}}` substituted from `settings.root_path`, so edits are live on the next hard-refresh — no restart, no rebuild. Because the whole `js/` directory is mounted, adding a file needs no `main.py` change — only a `<script>` tag in `index.html`.

- `js/app.js` — shell, shared helpers, and most sections.
- `js/usage_report.js` — the API Usage Report section only. Loads **after** `app.js` and relies on its globals (`api()`, `esc()`, `toast()`, `currentUser`, `syntaxHighlight()`, `registerCopyPayload()`/`copyPayload()`). It exists as a separate file because `app.js` had already grown past 2000 lines and this section is self-contained.

Conventions inside `app.js`:

- `navigateTo(page)` maps a sidebar `data-page` key to a `render*()` function and a title; every page renders its HTML into `#contentArea`.
- Shared helpers: `api(method, path, body)` (auth header + error unwrapping), `dbPrefix()` (current `/{alias}` segment), `esc()` for **all** interpolated values, `toast()`, `openModal()/closeModal()`.
- Most sections use modals for create/edit; **SQL Definitions and API Usage Report are the exceptions** — their list/detail views are full pages via `enterSqlDefView()` / `enterUsageView()`. Follow the neighbouring section's pattern rather than mixing both in one section.
- **API Usage Report is superuser-only.** `showApp()` hides `#navUsageReport` for everyone else; the backend's `get_current_active_superuser` remains the authority.
- Playwright specs in `tests/e2e/` cover the Query Console, raw-JSON copy buttons and the API Usage Report; run them after non-trivial Admin UI changes.
