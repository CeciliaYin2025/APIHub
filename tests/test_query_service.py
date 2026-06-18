"""Tests for query_service SQL normalization.

All fetch modes wrap a definition's SQL in a subquery (``... FROM (<sql>) sub_``),
so a trailing ``;`` breaks execution on every engine (e.g. MSSQL raises
"Incorrect syntax near ';'"). ``_normalize_sql`` strips trailing terminators.
"""

from app.services.query_service import _normalize_sql


def test_strips_trailing_semicolon():
    assert _normalize_sql("SELECT * FROM t;") == "SELECT * FROM t"


def test_strips_trailing_semicolon_with_whitespace_and_newline():
    assert _normalize_sql("SELECT * FROM t ;\n  ") == "SELECT * FROM t"


def test_strips_multiple_trailing_semicolons():
    assert _normalize_sql("SELECT * FROM t ; ;") == "SELECT * FROM t"


def test_leaves_clean_sql_untouched():
    assert _normalize_sql("SELECT a, b FROM t WHERE x = :x") == "SELECT a, b FROM t WHERE x = :x"


def test_preserves_non_trailing_semicolons():
    # Only trailing terminators are stripped; an internal ';' is left intact.
    assert _normalize_sql("SELECT ';' AS sep FROM t;") == "SELECT ';' AS sep FROM t"
