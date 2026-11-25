"""Download the BIRD Mini-Dev data from its official sources and verify it.

    python -m bench.fetch_data              # download what's missing, then verify
    python -m bench.fetch_data --verify     # only check what's already there

BIRD's data is 1.5 GB and licensed CC BY-SA 4.0 (the code here is MIT), so the repo
doesn't bundle it. This script fetches it from where BIRD publishes it (URLs in
bench/data_manifest.json, the Hugging Face one pinned to a fixed commit) and checks
every file against the SHA-256
hashes of the copy all published results were computed on. If a hash differs, the
numbers aren't expected to reproduce, and the script says so instead of carrying on.

What it downloads:
    mini_dev_sqlite.jsonl   ~5 MB    the 500 questions, gold SQL and official prompts
    minidev.zip             ~801 MB  the 11 SQLite databases (only the .sqlite files are
                                     extracted, about 1.5 GB on disk; the zip is deleted)

The big download resumes if interrupted (HTTP range request), so re-running after a
dropped connection doesn't start from zero.

Safety: downloaded files are treated as untrusted data. Nothing in them is executed, only
expected database names are extracted, each to a path this script builds itself (no paths
taken from the zip), and every file is hash-checked before use.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import zipfile
from pathlib import Path

import requests

from bench import config

MANIFEST = Path(__file__).with_name("data_manifest.json")
CHUNK = 1 << 20  # 1 MB


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(CHUNK), b""):
            h.update(block)
    return h.hexdigest()


def download(url: str, dest: Path, expected_bytes: int | None = None) -> None:
    """Stream url to dest, resuming a partial download (dest.part) if one exists."""
    part = dest.with_suffix(dest.suffix + ".part")
    have = part.stat().st_size if part.exists() else 0
    headers = {"Range": f"bytes={have}-"} if have else {}
    with requests.get(url, headers=headers, stream=True, timeout=60) as r:
        if have and r.status_code != 206:  # server ignored the range: start over
            have = 0
        r.raise_for_status()
        total = expected_bytes or (have + int(r.headers.get("Content-Length", 0)))
        mode = "ab" if have else "wb"
        done, last_pct = have, -1
        with part.open(mode) as out:
            for block in r.iter_content(CHUNK):
                out.write(block)
                done += len(block)
                pct = int(100 * done / total) if total else 0
                if pct != last_pct and pct % 5 == 0:
                    print(f"    {done / 2**20:,.0f} / {total / 2**20:,.0f} MB ({pct}%)", flush=True)
                    last_pct = pct
    part.replace(dest)


def check_questions(manifest: dict) -> list[str]:
    q = manifest["questions"]
    path = config.QUESTIONS_FILE
    if not path.exists():
        return [f"missing {path}"]
    if sha256(path) != q["sha256"]:
        return [f"{path.name}: hash differs from the manifest (a different dataset revision?)"]
    return []


def database_path(db_id: str) -> Path:
    return config.DATABASES_DIR / db_id / f"{db_id}.sqlite"


def check_databases(manifest: dict) -> list[str]:
    from bench.dataset import _index_databases

    _index_databases.cache_clear()
    found = _index_databases(config.DATABASES_DIR) if config.DATABASES_DIR.exists() else {}
    problems = []
    for db_id, want in manifest["databases"].items():
        path = found.get(db_id)
        if path is None:
            problems.append(f"missing {db_id}.sqlite")
        elif path.stat().st_size != want["bytes"] or sha256(path) != want["sha256"]:
            problems.append(f"{db_id}.sqlite: hash differs from the manifest")
    return problems


def fetch_questions(manifest: dict) -> None:
    path = config.QUESTIONS_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    print(f"  downloading questions -> {path}")
    download(manifest["sources"]["questions"]["url"], path, manifest["questions"]["bytes"])


def fetch_databases(manifest: dict) -> None:
    src = manifest["sources"]["databases"]
    downloads = config.BIRD_DIR / "_downloads"
    downloads.mkdir(parents=True, exist_ok=True)
    zpath = downloads / "minidev.zip"
    if not (zpath.exists() and zpath.stat().st_size == src["bytes"]):
        print(f"  downloading databases ({src['bytes'] / 2**20:,.0f} MB) from {src['url']}")
        download(src["url"], zpath, src["bytes"])
    wanted = set(manifest["databases"])
    print("  extracting the 11 .sqlite files")
    with zipfile.ZipFile(zpath) as z:
        for info in z.infolist():
            name = info.filename.rsplit("/", 1)[-1]
            db_id = name.removesuffix(".sqlite")
            if not name.endswith(".sqlite") or db_id not in wanted or "__MACOSX" in info.filename:
                continue
            out = database_path(db_id)  # our path, never one taken from the zip
            out.parent.mkdir(parents=True, exist_ok=True)
            with z.open(info) as fsrc, out.open("wb") as fdst:
                for block in iter(lambda: fsrc.read(CHUNK), b""):
                    fdst.write(block)
    zpath.unlink()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--verify", action="store_true", help="only verify, never download")
    args = ap.parse_args(argv)
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))

    print(f"BIRD Mini-Dev data in {config.BIRD_DIR}")
    q_problems = check_questions(manifest)
    if q_problems and not args.verify:
        fetch_questions(manifest)
        q_problems = check_questions(manifest)
    db_problems = check_databases(manifest)
    if db_problems and not args.verify:
        fetch_databases(manifest)
        db_problems = check_databases(manifest)

    problems = q_problems + db_problems
    if problems:
        print("FAIL:\n  " + "\n  ".join(problems))
        return 1
    print("OK: questions and all 11 databases match the manifest.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
