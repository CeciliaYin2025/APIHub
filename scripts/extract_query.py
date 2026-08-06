"""Stream a registered SQL definition's full result set to a local file.

Usage:
    export APIHUB_URL=https://ia-dev.sercomm.com/apihub-test
    export APIHUB_API_KEY=ahk_...          # or APIHUB_TOKEN=<JWT>
    python -m scripts.extract_query --db DW --query eo_detail \
        -p frozen_date=2026-08-01 --out D:/EO_Analysis/raw/eo_detail.csv

This is the bulk counterpart to the MCP tools. MCP returns rows into an LLM's
context, which is the wrong shape for a six-figure row count; this walks the
REST API's ``batch`` fetch mode and writes straight to disk, so neither side
ever holds the whole result set.

``fetch_mode=batch`` rather than ``all`` on purpose: ``all`` builds one JSON
document for every row (hundreds of MB on the E&O detail set) before the client
sees a byte.

Note on batch size: the server recomputes COUNT(*) on every batch, so the
per-request cost is nearly flat regardless of size. Larger batches are
therefore much cheaper overall — the default is deliberately high.

Exit codes: 0 ok, 1 the API rejected or failed the request, 2 bad usage,
3 the chosen output format needs a library that is not installed.
"""

import argparse
import csv
import os
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx

# Server-side ceiling for batch_size (app/schemas/query.py QueryRequest).
MAX_BATCH_SIZE = 10000
DEFAULT_BATCH_SIZE = 5000
SUFFIX_FORMATS = {".csv": "csv", ".parquet": "parquet", ".xlsx": "xlsx"}


def parse_params(pairs: list[str]) -> dict[str, str]:
    """Turn repeated ``-p key=value`` arguments into a bind-parameter dict.

    Values are passed through as strings — the definition's declared param
    types drive coercion server-side, so a date stays "2026-08-01" here.
    """
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


def iter_batches(
    base_url: str,
    db: str,
    query: str,
    params: dict[str, Any],
    batch_size: int,
    headers: dict[str, str],
    *,
    transport: httpx.BaseTransport | None = None,
    progress=None,
) -> Iterator[dict[str, Any]]:
    """Yield successive ``fetch_mode=batch`` responses until the set is drained."""
    url = f"{base_url.rstrip('/')}/api/v1/{db}/queries/execute"
    offset = 0
    with httpx.Client(transport=transport, timeout=300.0) as client:
        while True:
            resp = client.post(
                url,
                headers={**headers, "Content-Type": "application/json"},
                json={
                    "query_name": query,
                    "params": params,
                    "fetch_mode": "batch",
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
            yield payload

            rows = payload.get("data") or []
            if progress:
                progress(offset + len(rows), payload.get("total"))
            # Stop on a short batch even when has_more is set: without this a
            # server that reports more while returning nothing loops forever.
            if not payload.get("has_more") or len(rows) < batch_size:
                return
            offset += batch_size


def _resolve_format(out: Path, explicit: str | None) -> str:
    if explicit:
        return explicit
    fmt = SUFFIX_FORMATS.get(out.suffix.lower())
    if not fmt:
        raise ValueError(
            f"cannot infer format from {out.suffix!r}; use --format csv|parquet|xlsx "
            f"or name the file {'/'.join(SUFFIX_FORMATS)}"
        )
    return fmt


def _write_csv(out: Path, batches: Iterator[dict[str, Any]]) -> int:
    """Stream to disk: rows are written per batch, never accumulated."""
    written = 0
    # utf-8-sig so Excel opens Chinese column values without mojibake.
    with open(out, "w", newline="", encoding="utf-8-sig") as fh:
        writer = None
        for payload in batches:
            if writer is None:
                writer = csv.DictWriter(fh, fieldnames=payload.get("columns") or [])
                writer.writeheader()
            for row in payload.get("data") or []:
                writer.writerow(row)
                written += 1
    return written


def _write_buffered(out: Path, batches: Iterator[dict[str, Any]], fmt: str) -> int:
    """parquet/xlsx need the whole table in memory before writing."""
    columns: list[str] = []
    rows: list[dict[str, Any]] = []
    for payload in batches:
        columns = columns or (payload.get("columns") or [])
        rows.extend(payload.get("data") or [])

    if fmt == "parquet":
        try:
            import pandas as pd
        except ImportError as e:
            raise ModuleNotFoundError(
                "parquet output needs pandas + pyarrow: pip install pandas pyarrow"
            ) from e
        pd.DataFrame(rows, columns=columns or None).to_parquet(out, index=False)
    else:
        try:
            import pandas as pd
        except ImportError as e:
            raise ModuleNotFoundError("xlsx output needs pandas + openpyxl: pip install pandas openpyxl") from e
        pd.DataFrame(rows, columns=columns or None).to_excel(out, index=False)
    return len(rows)


def main(argv: list[str] | None = None, *, transport: httpx.BaseTransport | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m scripts.extract_query",
        description="Stream a registered SQL definition to a local CSV/Parquet/XLSX file.",
    )
    parser.add_argument("--base-url", default=os.environ.get("APIHUB_URL", ""), help="API Hub base URL (env APIHUB_URL)")
    parser.add_argument("--db", required=True, help="Data source alias, e.g. DW")
    parser.add_argument("--query", required=True, help="SQL definition name, e.g. eo_detail")
    parser.add_argument("-p", "--param", action="append", default=[], metavar="KEY=VALUE", help="Bind parameter")
    parser.add_argument("--out", required=True, type=Path, help="Output file path")
    parser.add_argument("--format", choices=sorted(set(SUFFIX_FORMATS.values())), help="Default: infer from --out")
    parser.add_argument(
        "--batch-size",
        type=int,
        default=DEFAULT_BATCH_SIZE,
        help=f"Rows per request (max {MAX_BATCH_SIZE}; larger is cheaper, default {DEFAULT_BATCH_SIZE})",
    )
    parser.add_argument("--quiet", action="store_true", help="Suppress progress output")
    args = parser.parse_args(argv)

    if not args.base_url:
        print("error: --base-url is required (or set APIHUB_URL)", file=sys.stderr)
        return 2
    if not 1 <= args.batch_size <= MAX_BATCH_SIZE:
        print(f"error: --batch-size must be between 1 and {MAX_BATCH_SIZE}", file=sys.stderr)
        return 2

    try:
        params = parse_params(args.param)
        headers = build_headers(os.environ.get("APIHUB_API_KEY", ""), os.environ.get("APIHUB_TOKEN", ""))
        fmt = _resolve_format(args.out, args.format)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    args.out.parent.mkdir(parents=True, exist_ok=True)

    def progress(done, total):
        if not args.quiet:
            print(f"  {done:,}/{total:,} rows", end="\r", file=sys.stderr, flush=True)

    batches = iter_batches(
        args.base_url,
        args.db,
        args.query,
        params,
        args.batch_size,
        headers,
        transport=transport,
        progress=progress,
    )

    try:
        written = _write_csv(args.out, batches) if fmt == "csv" else _write_buffered(args.out, batches, fmt)
    except ModuleNotFoundError as e:
        print(f"error: {e}", file=sys.stderr)
        args.out.unlink(missing_ok=True)
        return 3
    except (RuntimeError, httpx.HTTPError) as e:
        print(f"error: {e}", file=sys.stderr)
        args.out.unlink(missing_ok=True)
        return 1

    if not args.quiet:
        print(f"\nwrote {written:,} rows to {args.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
