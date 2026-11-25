"""Tests for the M4 reproduction tooling: data verification, safe extraction, comparison."""

import hashlib
import json
import zipfile

import pytest

from bench import config, fetch_data, reproduce


@pytest.fixture
def fake_bird(tmp_path, monkeypatch):
    """A BIRD-shaped folder with one tiny database, and a manifest that describes it."""
    bird = tmp_path / "bird"
    monkeypatch.setattr(config, "BIRD_DIR", bird)
    monkeypatch.setattr(config, "QUESTIONS_FILE", bird / "mini_dev_sqlite.jsonl")
    monkeypatch.setattr(config, "DATABASES_DIR", bird / "dev_databases")
    db_bytes = b"SQLite format 3\x00 pretend database"
    manifest = {
        "sources": {"databases": {"url": "https://example.invalid/minidev.zip", "bytes": 0}},
        "questions": {"sha256": hashlib.sha256(b"{}\n").hexdigest(), "bytes": 3},
        "databases": {"toy": {"sha256": hashlib.sha256(db_bytes).hexdigest(),
                              "bytes": len(db_bytes)}},
    }
    return bird, manifest, db_bytes


def test_sha256_matches_hashlib(tmp_path):
    p = tmp_path / "f.bin"
    p.write_bytes(b"x" * 3_000_000)
    assert fetch_data.sha256(p) == hashlib.sha256(b"x" * 3_000_000).hexdigest()


def test_missing_data_is_reported(fake_bird):
    _, manifest, _ = fake_bird
    assert fetch_data.check_questions(manifest) and fetch_data.check_databases(manifest)


def test_tampered_database_is_rejected(fake_bird):
    bird, manifest, db_bytes = fake_bird
    path = fetch_data.database_path("toy")
    path.parent.mkdir(parents=True)
    path.write_bytes(db_bytes)
    assert fetch_data.check_databases(manifest) == []
    path.write_bytes(db_bytes[:-1] + b"!")  # same size, different content
    assert "hash differs" in fetch_data.check_databases(manifest)[0]


def test_extraction_takes_only_expected_files_to_our_own_paths(fake_bird):
    bird, manifest, db_bytes = fake_bird
    downloads = bird / "_downloads"
    downloads.mkdir(parents=True)
    zpath = downloads / "minidev.zip"
    with zipfile.ZipFile(zpath, "w") as z:
        z.writestr("MINIDEV/dev_databases/toy/toy.sqlite", db_bytes)
        z.writestr("MINIDEV/dev_databases/evil/evil.sqlite", b"not in the manifest")
        z.writestr("../../escape/toy.sqlite.txt", b"path traversal attempt")
        z.writestr("__MACOSX/MINIDEV/._toy.sqlite", b"junk")
    manifest["sources"]["databases"]["bytes"] = zpath.stat().st_size  # skip the download
    fetch_data.fetch_databases(manifest)

    assert fetch_data.database_path("toy").read_bytes() == db_bytes
    assert not (config.DATABASES_DIR / "evil").exists()
    assert not (bird.parent / "escape").exists()
    assert not zpath.exists()  # the zip is removed after extraction
    assert fetch_data.check_databases(manifest) == []


def test_manifest_in_repo_covers_all_11_databases():
    m = json.loads(fetch_data.MANIFEST.read_text(encoding="utf-8"))
    assert len(m["databases"]) == 11
    assert "resolve/5b256b5a" in m["sources"]["questions"]["url"]  # pinned revision


def test_headline_picks_the_published_numbers():
    model = {"label_counts": {"correct": 46, "wrong_result": 308, "exec_error": 144,
                              "timeout": 2}}
    checks = {
        "detection_silent": {"all_rules": {"k": 68}, "own_rules_schema_value": {"k": 30}},
        "false_alarms": {"gold_all_rules": {"k": 9}, "gold_own_rules": {"k": 0}},
    }
    h = reproduce.headline(model, checks)
    assert h["silent errors caught"] == 68 and h["model crashed or timed out"] == 146


def test_every_committed_run_has_answers_and_expected_numbers():
    for run in reproduce.RUNS:
        assert (reproduce.PREDICTIONS_DIR / run / "predictions.jsonl").exists()
        assert reproduce.committed(run)["silent errors caught"] > 0
