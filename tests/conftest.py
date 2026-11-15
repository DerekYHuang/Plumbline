"""Shared fixture: a small school database, profiled once per test session.

Modelled on BIRD's california_schools so the rule tests read like the real failures
from M0 (wrong capitalisation, invented codes, missing joins, double counting).
"""

import sqlite3

import pytest

from plumbline.profiler import profile_database

SCHEMA = """
CREATE TABLE schools (
    "CDSCode" TEXT PRIMARY KEY,
    "School" TEXT,
    "County" TEXT,
    "FundingType" TEXT,
    "OpenDate" TEXT,
    "Virtual" TEXT
);
CREATE TABLE frpm (
    "CDSCode" TEXT PRIMARY KEY REFERENCES schools("CDSCode"),
    "Enrollment (K-12)" INTEGER,
    "Free Meal Count (K-12)" INTEGER
);
CREATE TABLE satscores (
    id INTEGER PRIMARY KEY,
    cds TEXT REFERENCES schools("CDSCode"),
    rtype TEXT,
    "NumTstTakr" INTEGER,
    "AvgScrMath" INTEGER
);
"""


@pytest.fixture(scope="session")
def school_catalog(tmp_path_factory):
    path = tmp_path_factory.mktemp("db") / "schools.sqlite"
    conn = sqlite3.connect(path)
    conn.executescript(SCHEMA)
    counties = ["Alameda", "Orange", "Fresno", "Kern"]
    funding = ["Directly funded", "Locally funded", None]
    schools = []
    # 300 schools: enough distinct codes that CDSCode is too big to list (max 200),
    # so code checks go through the length/format path, as they do on real BIRD data.
    for i in range(300):
        code = f"{i % 58 + 1:02d}{i:012d}"  # 14 digits, often starting with 0
        schools.append(
            (code, f"School {i}", counties[i % 4], funding[i % 3],
             # 300 distinct dates: too many to list, so dates are judged by format
             f"{1980 + i % 40}-{i % 12 + 1:02d}-{i % 28 + 1:02d}", "F" if i % 5 else "N")
        )
    conn.executemany("INSERT INTO schools VALUES (?, ?, ?, ?, ?, ?)", schools)
    conn.executemany(
        "INSERT INTO frpm VALUES (?, ?, ?)",
        [(s[0], 100 + i, i % 50) for i, s in enumerate(schools)],
    )
    # Two satscores rows per school (rtype S and D): joining schools -> satscores fans out.
    conn.executemany(
        "INSERT INTO satscores (cds, rtype, NumTstTakr, AvgScrMath) VALUES (?, ?, ?, ?)",
        [(s[0], rt, 10 + i, 400 + i % 200) for i, s in enumerate(schools) for rt in ("S", "D")],
    )
    conn.commit()
    conn.close()
    return profile_database(path)
