"""The `plumbline check` command and the check_sql result format."""

import json

import pytest

from plumbline.cli import main


@pytest.fixture
def catalog_file(school_catalog, tmp_path):
    return school_catalog.save(tmp_path / "schools.json")


def test_check_pass_exit_0(catalog_file, capsys):
    code = main(["check", str(catalog_file), "SELECT School FROM schools WHERE County = 'Kern'"])
    assert code == 0
    assert capsys.readouterr().out.startswith("PASS")


def test_check_fail_exit_1_with_hint(catalog_file, capsys):
    sql = "SELECT School FROM schools WHERE FundingType = 'Locally Funded'"
    assert main(["check", str(catalog_file), sql]) == 1
    out = capsys.readouterr().out
    assert "FAIL" in out and "VALUE_CASE" in out and "hint: Use 'Locally funded'." in out


def test_check_json_output(catalog_file, capsys):
    sql = "SELECT School FROM schools WHERE FundingType = 'Locally Funded'"
    main(["check", str(catalog_file), sql, "--json"])
    data = json.loads(capsys.readouterr().out)
    assert data["passed"] is False
    assert data["findings"][0]["rule"] == "VALUE_CASE"
    assert data["findings"][0]["evidence"]["actual"] == "Locally funded"


def test_check_sql_from_file(catalog_file, tmp_path):
    q = tmp_path / "q.sql"
    q.write_text("SELECT * FROM satscore", encoding="utf-8")
    assert main(["check", str(catalog_file), f"@{q}"]) == 1


def test_check_unparseable_exit_2(catalog_file):
    assert main(["check", str(catalog_file), "SELEC FROM"]) == 2


def test_check_missing_target_exit_2(tmp_path):
    assert main(["check", str(tmp_path / "nope.json"), "SELECT 1"]) == 2


def test_check_against_sqlite_caches_catalog(tmp_path):
    import sqlite3

    db = tmp_path / "t.sqlite"
    conn = sqlite3.connect(db)
    conn.executescript("CREATE TABLE t (s TEXT); INSERT INTO t VALUES ('Yes'), ('No');")
    conn.close()
    assert main(["check", str(db), "SELECT * FROM t WHERE s = 'yes'"]) == 1
    assert (tmp_path / "t.catalog.json").exists()
