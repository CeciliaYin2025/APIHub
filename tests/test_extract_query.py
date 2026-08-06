"""Bulk extraction CLI: stream a definition's full result set to a local file."""

import csv
import json

import httpx
import pytest

from scripts.extract_query import build_headers, iter_batches, main, parse_params


def _transport(handler):
    return httpx.MockTransport(handler)


# ── parameter parsing ────────────────────────────────────────────────────────


def test_parse_params_splits_on_first_equals_only():
    assert parse_params(["frozen_date=2026-08-01", "pid=A=B"]) == {
        "frozen_date": "2026-08-01",
        "pid": "A=B",
    }


def test_parse_params_empty_value_is_kept():
    assert parse_params(["bu="]) == {"bu": ""}


def test_parse_params_rejects_missing_equals():
    with pytest.raises(ValueError, match="expected key=value"):
        parse_params(["frozen_date"])


# ── auth headers ─────────────────────────────────────────────────────────────


def test_api_key_takes_precedence_and_uses_its_own_header():
    assert build_headers("ahk_abc", "jwt") == {"X-API-Key": "ahk_abc"}


def test_token_falls_back_to_bearer():
    assert build_headers("", "jwt") == {"Authorization": "Bearer jwt"}


def test_no_credential_raises():
    with pytest.raises(ValueError, match="APIHUB_API_KEY or APIHUB_TOKEN"):
        build_headers("", "")


# ── batch iteration ──────────────────────────────────────────────────────────


def _paged_handler(total, batch_size, seen=None):
    def handler(request):
        body = json.loads(request.read())
        if seen is not None:
            seen.append(body)
        offset = body["batch_offset"]
        rows = [{"ID": i, "V": f"v{i}"} for i in range(offset, min(offset + batch_size, total))]
        return httpx.Response(
            200,
            json={
                "success": True,
                "query_name": body["query_name"],
                "fetch_mode": "batch",
                "total": total,
                "batch_offset": offset,
                "batch_size": batch_size,
                "has_more": (offset + batch_size) < total,
                "columns": ["ID", "V"],
                "data": rows,
            },
        )

    return handler


def test_iter_batches_walks_until_has_more_is_false():
    seen = []
    batches = list(
        iter_batches(
            "http://hub",
            "DW",
            "eo_detail",
            {"frozen_date": "2026-08-01"},
            batch_size=10,
            headers={},
            transport=_transport(_paged_handler(25, 10, seen)),
        )
    )
    assert [len(b["data"]) for b in batches] == [10, 10, 5]
    assert [s["batch_offset"] for s in seen] == [0, 10, 20]
    assert {s["fetch_mode"] for s in seen} == {"batch"}
    assert seen[0]["params"] == {"frozen_date": "2026-08-01"}


def test_iter_batches_handles_an_empty_result_set():
    batches = list(
        iter_batches(
            "http://hub", "DW", "q", {}, batch_size=10, headers={}, transport=_transport(_paged_handler(0, 10))
        )
    )
    assert len(batches) == 1
    assert batches[0]["data"] == []


def test_iter_batches_stops_on_a_short_batch_even_if_has_more_lies():
    # Guard against an infinite loop: the server says there is more but returns
    # nothing, which would otherwise re-request the same offset forever.
    def handler(request):
        return httpx.Response(
            200,
            json={"total": 99, "has_more": True, "columns": ["ID"], "data": [], "fetch_mode": "batch"},
        )

    batches = list(
        iter_batches("http://hub", "DW", "q", {}, batch_size=10, headers={}, transport=_transport(handler))
    )
    assert len(batches) == 1


def test_iter_batches_raises_on_http_error():
    def handler(request):
        return httpx.Response(403, json={"detail": "Permission denied: read on query:eo_detail"})

    with pytest.raises(RuntimeError, match="Permission denied"):
        list(iter_batches("http://hub", "DW", "q", {}, batch_size=10, headers={}, transport=_transport(handler)))


# ── end to end through main() ────────────────────────────────────────────────


def test_main_writes_csv_with_header_and_all_rows(tmp_path, monkeypatch):
    monkeypatch.setenv("APIHUB_TOKEN", "jwt")
    out = tmp_path / "eo.csv"
    rc = main(
        ["--base-url", "http://hub", "--db", "DW", "--query", "eo_detail", "--out", str(out), "--batch-size", "10"],
        transport=_transport(_paged_handler(25, 10)),
    )
    assert rc == 0
    with open(out, newline="", encoding="utf-8-sig") as fh:
        rows = list(csv.reader(fh))
    assert rows[0] == ["ID", "V"]
    assert len(rows) == 26
    assert rows[1] == ["0", "v0"]
    assert rows[-1] == ["24", "v24"]


def test_main_writes_only_a_header_for_an_empty_result(tmp_path, monkeypatch):
    monkeypatch.setenv("APIHUB_TOKEN", "jwt")
    out = tmp_path / "empty.csv"
    rc = main(
        ["--base-url", "http://hub", "--db", "DW", "--query", "q", "--out", str(out)],
        transport=_transport(_paged_handler(0, 10)),
    )
    assert rc == 0
    assert out.read_text(encoding="utf-8-sig").strip() == "ID,V"


def test_main_passes_params_through(tmp_path, monkeypatch):
    monkeypatch.setenv("APIHUB_TOKEN", "jwt")
    seen = []
    out = tmp_path / "p.csv"
    main(
        [
            "--base-url", "http://hub", "--db", "DW", "--query", "eo_summary",
            "--out", str(out), "-p", "frozen_date=2026-08-01", "-p", "ou=SC,SN",
        ],
        transport=_transport(_paged_handler(5, 10, seen)),
    )
    assert seen[0]["params"] == {"frozen_date": "2026-08-01", "ou": "SC,SN"}


def test_main_rejects_a_bad_param_without_writing_anything(tmp_path, monkeypatch):
    monkeypatch.setenv("APIHUB_TOKEN", "jwt")
    out = tmp_path / "nope.csv"
    rc = main(
        ["--base-url", "http://hub", "--db", "DW", "--query", "q", "--out", str(out), "-p", "oops"],
        transport=_transport(_paged_handler(5, 10)),
    )
    assert rc == 2
    assert not out.exists()


def test_main_reports_missing_credentials(tmp_path, monkeypatch):
    monkeypatch.delenv("APIHUB_TOKEN", raising=False)
    monkeypatch.delenv("APIHUB_API_KEY", raising=False)
    rc = main(
        ["--base-url", "http://hub", "--db", "DW", "--query", "q", "--out", str(tmp_path / "x.csv")],
        transport=_transport(_paged_handler(5, 10)),
    )
    assert rc == 2


def test_main_surfaces_a_server_error_as_exit_1(tmp_path, monkeypatch):
    monkeypatch.setenv("APIHUB_TOKEN", "jwt")

    def handler(request):
        return httpx.Response(500, json={"detail": "boom"})

    rc = main(
        ["--base-url", "http://hub", "--db", "DW", "--query", "q", "--out", str(tmp_path / "x.csv")],
        transport=_transport(handler),
    )
    assert rc == 1


def test_main_infers_format_from_the_output_suffix(tmp_path, monkeypatch):
    monkeypatch.setenv("APIHUB_TOKEN", "jwt")
    out = tmp_path / "inferred.csv"
    assert (
        main(
            ["--base-url", "http://hub", "--db", "DW", "--query", "q", "--out", str(out)],
            transport=_transport(_paged_handler(3, 10)),
        )
        == 0
    )
    assert out.exists()


def test_main_rejects_an_unsupported_suffix(tmp_path, monkeypatch):
    monkeypatch.setenv("APIHUB_TOKEN", "jwt")
    rc = main(
        ["--base-url", "http://hub", "--db", "DW", "--query", "q", "--out", str(tmp_path / "x.txt")],
        transport=_transport(_paged_handler(3, 10)),
    )
    assert rc == 2
