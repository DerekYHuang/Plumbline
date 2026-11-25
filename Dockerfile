# Plumbline: one image that reproduces the published numbers, and runs the checker.
#
#   docker build -t plumbline .
#   docker run --rm -v "$PWD/data:/app/data" plumbline            # reproduce (default)
#   docker run --rm -v "$PWD/data:/app/data" plumbline plumbline check \
#       data/catalogs/california_schools.json "SELECT ..."         # check one query
#
# The BIRD data is NOT in the image: it's 1.5 GB, and it's licensed CC BY-SA 4.0 while the
# code is MIT, so it's fetched from its source rather than bundled.
# Mount a folder at /app/data: the first run downloads and verifies the data into it
# (bench.fetch_data), later runs reuse it. On Windows, mount a folder on a drive with
# space; Docker Desktop keeps its own storage on C:.

# Pinned to an exact version so the image builds the same way next year.
FROM python:3.12.7-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Install dependencies first, in their own layer: code changes then don't re-download them.
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN pip install ".[fanout]"

# The benchmark harness and the committed results it compares against.
COPY bench ./bench
COPY results ./results

# Don't run as root: the container only needs to read code and write under /app/data.
RUN useradd --create-home --uid 1000 plumbline \
    && mkdir -p /app/data /app/reproduce_out /app/bench/runs \
    && chown -R plumbline /app
USER plumbline

CMD ["python", "-m", "bench.reproduce"]
