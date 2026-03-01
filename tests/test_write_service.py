"""Tests for write_service input validation."""

import pytest

from app.services.write_service import validate_table_name


def test_valid_table_names():
    assert validate_table_name("EMPLOYEES") == "EMPLOYEES"
    assert validate_table_name("HR.EMPLOYEES") == "HR.EMPLOYEES"
    assert validate_table_name("my_table_123") == "my_table_123"


def test_invalid_table_names():
    with pytest.raises(ValueError, match="Invalid table name"):
        validate_table_name("'; DROP TABLE --")

    with pytest.raises(ValueError, match="Invalid table name"):
        validate_table_name("")

    with pytest.raises(ValueError, match="Invalid table name"):
        validate_table_name("123_bad")

    with pytest.raises(ValueError, match="Invalid table name"):
        validate_table_name("table name with spaces")
