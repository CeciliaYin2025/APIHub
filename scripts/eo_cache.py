"""Client-side cache for E&O query results, keyed on the frozen period.

Usage:
    export APIHUB_URL=https://ia-dev.sercomm.com/apihub
    export APIHUB_API_KEY=ahk_...          # or APIHUB_TOKEN=<JWT>

    python -m scripts.eo_cache fetch --query eo_red_items \
        -p frozen_date=2026-06-26 -p compare_date=2026-05-29 -p ou=SP
    python -m scripts.eo_cache status
    python -m scripts.eo_cache clear --query eo_red_items
    python -m scripts.eo_cache clear --all

Why this exists: the E&O definitions that matter for analysis are the slow ones
(``eo_item_history`` ~35s, ``eo_red_items`` ~14s across all sites) because the
underlying view has no index on Item_No and the TVF is recomputed per call.
Frozen periods are immutable once closed, so re-running the same query for a
closed period can only ever return the same rows — the second call is pure
waste. This caches those results next to the caller instead of materialising
tables inside SQL Server.

**The latest period is never cached.** It can still be re-run and corrected
upstream, so a cached copy would silently go stale in exactly the case an
analyst is most likely to be looking at. A request is cacheable only when it
carries at least one explicit date parameter and every one of them is strictly
older than the current ``eo_max_frozen_date``. Queries with no date parameter
(``eo_trends``, ``eo_item_history``, a bare ``eo_dimensions``) always include
the latest period by construction, so they are always fetched live.

``--refresh`` forces one entry to be refetched; ``clear`` drops entries wholesale.

This is a client-side tool. It talks to the REST API over HTTP and is not part
of the server image's runtime.

Exit codes: 0 ok, 1 the API rejected or failed the request, 2 bad usage.
"""

import argparse
import gzip
import hashlib
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

import httpx

DEFAULT_DB = "DW"
DEFAULT_CACHE_DIR = Path.home() / ".cache" / "apihub-eo"
# Above this the result belongs in a file, not in a cache entry an agent will
# read back into a context window. scripts/extract_query.py is the tool for that.
DEFAULT_MAX_ROWS = 50_000
LATEST_QUERY = "eo_max_frozen_date"
# Parameters that name a frozen period. Any request carrying one of these can be
# cached; anything else sweeps the latest period and cannot.
DATE_PARAMS = ("frozen_date", "compare_date")


def parse_params(pairs: list[str]) -> dict[str, str]:
    """Turn repeated ``-p key=value`` arguments into a bind-parameter dict."""
    params: dict[str, str] = {}
    for pair in pairs or []:
        if "=" not in pair:
            raise ValueError(f"expected key=value, got {pair!r}")
        key, value = pair.split("=", 1)
        params[key.strip()] = value
    return params


def build_headers(api_key: str, token: str) -> dict[str, str]:
    """Prefer an API key; fall back to a JWT. One of the two is required."""
    if api_key:
        return {"X-API-Key": api_key}
    if token:
        return {"Authorization": f"Bearer {token}"}
    raise ValueError("no credential: set APIHUB_API_KEY or APIHUB_TOKEN")


def execute(
    base_url: str,
    db: str,
    query: str,
    params: dict[str, Any],
    headers: dict[str, str],
    *,
    fetch_mode: str = "batch",
    batch_size: int = 10000,
    max_rows: int = DEFAULT_MAX_ROWS,
    transport: httpx.BaseTransport | None = None,
) -> dict[str, Any]:
    """Run a definition and return one merged ``{columns, data, total}`` payload.

    Uses ``batch`` so a wide result never becomes a single huge JSON document
    server-side, and stops early once ``max_rows`` is exceeded so a mis-aimed
    query cannot fill the cache directory with a six-figure row set.
    """
    url = f"{base_url.rstrip('/')}/api/v1/{db}/queries/execute"
    columns: list[str] = []
    rows: list[dict[str, Any]] = []
    total = None
    offset = 0
    with httpx.Client(transport=transport, timeout=600.0) as client:
        while True:
            resp = client.post(
                url,
                headers={**headers, "Content-Type": "application/json"},
                json={
                    "query_name": query,
                    "params": params,
                    "fetch_mode": fetch_mode,
                    "batch_size": batch_size,
                    "batch_offset": offset,
                },
            )
            if resp.status_code >= 400:
                try:
                    detail = resp.json().get("detail", resp.text)
                except ValueError:
                    detail = resp.text
                raise RuntimeError(f"[{resp.status_code}] {detail}")
            payload = resp.json()
            columns = payload.get("columns") or columns
            total = payload.get("total", total)
            batch = payload.get("data") or []
            rows.extend(batch)
            if len(rows) > max_rows:
                raise RuntimeError(
                    f"result exceeds --max-rows ({max_rows}): {query} returned {total} rows. "
                    f"Use scripts/extract_query.py to stream it to a file instead of caching it."
                )
            # Stop on a short batch even when has_more is set: without this a
            # server that reports more while returning nothing loops forever.
            if not payload.get("has_more") or len(batch) < batch_size:
                break
            offset += batch_size
    return {"columns": columns, "data": rows, "total": total}


def latest_period(base_url: str, db: str, headers: dict[str, str], **kw) -> str | None:
    """The current max frozen date, as reported by the API. None if unavailable."""
    try:
        payload = execute(base_url, db, LATEST_QUERY, {}, headers, fetch_mode="all", **kw)
    except RuntimeError:
        return None
    rows = payload.get("data") or []
    if not rows:
        return None
    value = next(iter(rows[0].values()), None)
    return str(value)[:10] if value else None


def cacheable(params: dict[str, str], latest: str | None) -> tuple[bool, str]:
    """Decide whether this request may be cached, and say why not when it may not."""
    dates = {k: v for k, v in params.items() if k in DATE_PARAMS and v}
    if not dates:
        return False, "no explicit date parameter — the query spans the latest period"
    if latest is None:
        return False, "could not determine the latest frozen date"
    newest = max(dates.values())
    if newest >= latest:
        return False, f"includes the latest period ({latest}), which can still be revised"
    return True, ""


def entry_path(cache_dir: Path, db: str, query: str, params: dict[str, str]) -> Path:
    """One file per (db, query, exact parameter set)."""
    canonical = json.dumps(params, sort_keys=True, ensure_ascii=False)
    digest = hashlib.sha256(f"{db}|{query}|{canonical}".encode()).hexdigest()[:16]
    return cache_dir / db / query / f"{digest}.json.gz"


def read_entry(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        with gzip.open(path, "rt", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None  # a corrupt entry is a cache miss, never a crash


def write_entry(path: Path, entry: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with gzip.open(tmp, "wt", encoding="utf-8") as fh:
        json.dump(entry, fh, ensure_ascii=False, default=str)
    tmp.replace(path)  # atomic: a killed run never leaves a half-written entry


def cmd_fetch(args, headers: dict[str, str]) -> int:
    params = parse_params(args.param)
    cache_dir = Path(args.cache_dir)
    latest = latest_period(args.url, args.db, headers)
    ok, reason = cacheable(params, latest)
    path = entry_path(cache_dir, args.db, args.query, params)

    if ok and not args.refresh:
        entry = read_entry(path)
        if entry is not None:
            print(
                f"cache HIT  {args.query} {params} ({entry.get('total')} rows, cached {entry.get('fetched_at')})",
                file=sys.stderr,
            )
            emit(entry, args)
            return 0

    started = time.time()
    payload = execute(args.url, args.db, args.query, params, headers, max_rows=args.max_rows)
    elapsed = time.time() - started
    entry = {
        "db": args.db,
        "query": args.query,
        "params": params,
        "columns": payload["columns"],
        "data": payload["data"],
        "total": payload["total"],
        "fetched_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "elapsed_seconds": round(elapsed, 1),
        "latest_period_at_fetch": latest,
    }
    if ok:
        write_entry(path, entry)
        note = f"cached -> {path}"
    else:
        note = f"NOT cached ({reason})"
    print(
        f"cache MISS {args.query} {params} ({payload['total']} rows, {elapsed:.1f}s) {note}",
        file=sys.stderr,
    )
    emit(entry, args)
    return 0


def emit(entry: dict[str, Any], args) -> None:
    body = entry if args.with_meta else {k: entry[k] for k in ("columns", "data", "total")}
    text = json.dumps(body, ensure_ascii=False, indent=2 if args.pretty else None, default=str)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
        print(f"wrote {args.out}", file=sys.stderr)
    else:
        print(text)


def cmd_status(args, headers: dict[str, str]) -> int:
    cache_dir = Path(args.cache_dir)
    entries = sorted(cache_dir.rglob("*.json.gz"))
    if not entries:
        print(f"cache empty ({cache_dir})")
        return 0
    total_bytes = 0
    print(f"{len(entries)} entrie(s) in {cache_dir}\n")
    print(f"  {'QUERY':22s} {'ROWS':>8s} {'SIZE':>9s} {'FETCHED':20s} PARAMS")
    for path in entries:
        entry = read_entry(path)
        size = path.stat().st_size
        total_bytes += size
        if entry is None:
            print(f"  {path.name:22s} {'?':>8s} {size / 1024:>8.0f}K {'(unreadable)':20s}")
            continue
        params = ", ".join(f"{k}={v}" for k, v in sorted(entry.get("params", {}).items()))
        print(
            f"  {entry.get('query', '?'):22s} {entry.get('total', '?'):>8} "
            f"{size / 1024:>8.0f}K {entry.get('fetched_at', '?'):20s} {params}"
        )
    print(f"\ntotal {total_bytes / 1024 / 1024:.1f} MB")
    return 0


def cmd_clear(args, headers: dict[str, str]) -> int:
    cache_dir = Path(args.cache_dir)
    if not (args.all or args.query):
        print("clear needs --query <name> or --all", file=sys.stderr)
        return 2
    root = cache_dir if args.all else cache_dir / args.db / args.query
    entries = sorted(root.rglob("*.json.gz")) if root.exists() else []
    for path in entries:
        path.unlink()
    print(f"removed {len(entries)} entrie(s) from {root}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m scripts.eo_cache",
        description="Fetch E&O query results with a local cache keyed on the frozen period.",
    )
    parser.add_argument("--url", default=os.environ.get("APIHUB_URL", ""), help="API base URL (APIHUB_URL)")
    parser.add_argument("--db", default=DEFAULT_DB, help=f"database alias (default {DEFAULT_DB})")
    parser.add_argument(
        "--cache-dir",
        default=os.environ.get("EO_CACHE_DIR", str(DEFAULT_CACHE_DIR)),
        help=f"cache directory (EO_CACHE_DIR, default {DEFAULT_CACHE_DIR})",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    fetch = sub.add_parser("fetch", help="run a definition, reusing a cached result when allowed")
    fetch.add_argument("--query", required=True, help="SQL definition name, e.g. eo_red_items")
    fetch.add_argument("-p", "--param", action="append", default=[], metavar="KEY=VALUE")
    fetch.add_argument("--refresh", action="store_true", help="ignore any cached entry and refetch")
    fetch.add_argument("--max-rows", type=int, default=DEFAULT_MAX_ROWS)
    fetch.add_argument("--out", help="write JSON here instead of stdout")
    fetch.add_argument("--pretty", action="store_true")
    fetch.add_argument("--with-meta", action="store_true", help="include cache metadata in the output")
    fetch.set_defaults(func=cmd_fetch)

    status = sub.add_parser("status", help="list cached entries")
    status.set_defaults(func=cmd_status)

    clear = sub.add_parser("clear", help="drop cached entries")
    clear.add_argument("--query", help="only this definition")
    clear.add_argument("--all", action="store_true", help="everything, every definition and alias")
    clear.set_defaults(func=cmd_clear)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    needs_api = args.command == "fetch"
    if needs_api and not args.url:
        print("no API URL: set APIHUB_URL or pass --url", file=sys.stderr)
        return 2
    try:
        headers = (
            build_headers(os.environ.get("APIHUB_API_KEY", ""), os.environ.get("APIHUB_TOKEN", "")) if needs_api else {}
        )
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    try:
        return args.func(args, headers)
    except ValueError as exc:
        print(f"bad usage: {exc}", file=sys.stderr)
        return 2
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
