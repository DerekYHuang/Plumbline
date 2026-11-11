"""Benchmark tooling for Plumbline (not part of the installed package).

M0 baseline pipeline, run from the repo root:

    python -m bench.verify_gold     # 1. do all 500 supplied gold queries run?
    python -m bench.generate        # 2. have the model write SQL for each question
    python -m bench.score <run>     # 3. execute + compare -> right/wrong labels

Module map:
    config.py         where the data lives, default model settings
    dataset.py        load BIRD Mini-Dev questions, find each SQLite file
    execute.py        run SQL safely with a timeout; BIRD's result comparison
    extract.py        pull the SQL out of a model's free-text answer
    ollama_client.py  tiny HTTP client for the local Ollama server
"""
