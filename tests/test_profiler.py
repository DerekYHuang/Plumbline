"""Tests for the profiler and the catalog, against a small throwaway SQLite database.

The toy database is built to contain each case the M2 checks will rely on: a value
list with exact case, a zero-padded ID, ISO dates, NULLs, a mixed-type column, a
high-cardinality column, a BLOB, and names with spaces.
"""

import sqlite3

import pytest

from plumbline.catalog import Catalog
from plumbline.cli import main
from plumbline.profiler import profile_database, value_shape


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "schools.sqlite"
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE schools (
            "CDSCode" TEXT PRIMARY KEY,
            "FundingType" TEXT,
            "OpenDate" DATE,
            "Enrollment (K-12)" INTEGER,
            mixed,
            note TEXT,
            logo BLOB
        );
        CREATE TABLE scores (
            id INTEGER PRIMARY KEY,
            cds TEXT REFERENCES schools("CDSCode"),
            score REAL
        );
        """
    )
    rows = [
        ("01611920113902", "Locally funded", "1996-01-05", 120, 1, "n0", b"\x00"),
        ("01612590125716", "Directly funded", "2001-09-01", 80, "x", "n1", None),
        ("19647331930130", "Locally funded", None, None, 2.5, "n2", None),
        ("33671243330263", None, "1980-07-15", 300, None, "n3", None),
    ]
    conn.executemany("INSERT INTO schools VALUES (?, ?, ?, ?, ?, ?, ?)", rows)
    conn.executemany(
        "INSERT INTO scores VALUES (?, ?, ?)",
        [(i, rows[i % 4][0], float(i)) for i in range(1, 51)],
    )
    conn.commit()
    conn.close()
    return path


@pytest.fixture
def cat(db):
    # max_values=10: small enough that scores.id (50 values) counts as high-cardinality.
    return profile_database(db, max_values=10)


# --- value_shape ------------------------------------------------------------
@pytest.mark.parametrize(
    "value, shape",
    [
        ("01611920113902", "9{14}"),
        ("1996-01-05", "9{4}-9{2}-9{2}"),
        ("TR010", "A{2}9{3}"),
        ("Locally funded", "Aa{6} a{6}"),
        ("", ""),
        ("a", "a"),
    ],
)
def test_value_shape(value, shape):
    assert value_shape(value) == shape


# --- tables, keys -------------------------------------------------------------
def test_tables_and_row_counts(cat):
    assert [t.name for t in cat.tables] == ["schools", "scores"]
    assert cat.table("schools").row_count == 4
    assert cat.table("SCORES").row_count == 50  # lookup ignores case


def test_primary_and_foreign_keys(cat):
    assert cat.table("schools").primary_key == ["CDSCode"]
    fk = cat.table("scores").foreign_keys
    assert fk == [{"columns": ["cds"], "ref_table": "schools", "ref_columns": ["CDSCode"]}]


def test_unique_detection(cat):
    assert cat.column("schools", "CDSCode").is_unique
    assert cat.column("schools", "CDSCode").is_primary_key
    assert cat.column("scores", "id").is_unique
    assert not cat.column("scores", "cds").is_unique  # repeats
    assert not cat.column("schools", "OpenDate").is_unique  # has a NULL


# --- counts and nulls -----------------------------------------------------------
def test_null_rate_and_distinct(cat):
    col = cat.column("schools", "FundingType")
    assert col.null_count == 1
    assert col.null_rate == pytest.approx(0.25)
    assert col.distinct_count == 2


# --- value lists ------------------------------------------------------------------
def test_complete_value_list_keeps_exact_case_and_counts(cat):
    col = cat.column("schools", "fundingtype")  # lookup ignores case
    assert col.values_complete
    assert col.values == [["Locally funded", 2], ["Directly funded", 1]]


def test_high_cardinality_keeps_only_top_values(cat):
    col = cat.column("scores", "id")
    assert col.distinct_count == 50
    assert not col.values_complete
    assert 0 < len(col.values) <= 20


def test_blob_column_has_no_value_list(cat):
    col = cat.column("schools", "logo")
    assert col.storage_types == {"blob": 1}
    assert col.values == []


# --- types and ranges -------------------------------------------------------------
def test_mixed_storage_types_are_counted(cat):
    col = cat.column("schools", "mixed")
    assert col.storage_types == {"integer": 1, "text": 1, "real": 1}
    assert (col.num_min, col.num_max) == (1, 2.5)
    assert col.text_min == "x"


def test_numeric_range(cat):
    col = cat.column("schools", "Enrollment (K-12)")  # name with spaces and brackets
    assert (col.num_min, col.num_max) == (80, 300)
    assert col.text_min is None


def test_date_range_and_shape(cat):
    col = cat.column("schools", "OpenDate")
    assert (col.text_min, col.text_max) == ("1980-07-15", "2001-09-01")
    assert col.shapes == [["9{4}-9{2}-9{2}", 1.0]]


def test_id_shape_shows_length(cat):
    col = cat.column("schools", "CDSCode")
    assert col.shapes == [["9{14}", 1.0]]
    assert (col.text_len_min, col.text_len_max) == (14, 14)


# --- safety, persistence, CLI --------------------------------------------------------
def test_profiling_does_not_modify_the_database(db):
    before = db.read_bytes()
    profile_database(db)
    assert db.read_bytes() == before


def test_catalog_round_trips_through_json(cat, tmp_path):
    path = cat.save(tmp_path / "c.json")
    again = Catalog.load(path)
    assert again == cat
    assert not again.is_stale()


def test_catalog_notices_changed_database(cat, db):
    conn = sqlite3.connect(db)
    conn.execute("INSERT INTO scores VALUES (999, NULL, NULL)")
    conn.commit()
    conn.close()
    assert cat.is_stale()


def test_old_catalog_version_is_rejected(cat):
    d = cat.to_dict()
    d["version"] = 0
    with pytest.raises(ValueError, match="version"):
        Catalog.from_dict(d)


def test_cli_profile_writes_catalog(db, tmp_path, capsys):
    out = tmp_path / "out" / "schools.json"
    assert main(["profile", str(db), "-o", str(out)]) == 0
    assert out.exists()
    assert Catalog.load(out).table("schools") is not None
    assert "2 tables" in capsys.readouterr().out


def test_cli_profile_missing_file(tmp_path):
    assert main(["profile", str(tmp_path / "nope.sqlite")]) == 2
