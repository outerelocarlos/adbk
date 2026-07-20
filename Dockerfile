# A single, minimal image that can run the CLI and the development tools.
# It works on linux/amd64 and linux/arm64: ADB is provided by Debian's
# android-tools-adb package, which exists for both architectures (Google's own
# platform-tools archive is x86_64-only, so we do not use it inside the image).
FROM python:3.12-slim

# Marks the environment as a container even when /.dockerenv is not visible,
# and keeps the virtualenv outside /app so a bind-mounted source tree (used by
# the dev service) does not shadow the installed dependencies.
ENV ADBK_CONTAINER=1 \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH=/opt/venv/bin:$PATH \
    PYTHONUNBUFFERED=1

# ADB client + TLS roots. --no-install-recommends keeps the image small.
RUN apt-get update \
    && apt-get install -y --no-install-recommends android-tools-adb ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# uv provides reproducible, cache-friendly dependency installation.
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

# Install dependencies first (cached until the lockfile changes), then the
# project itself. --frozen fails the build if uv.lock is out of date.
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-install-project
COPY src ./src
RUN uv sync --frozen

# Run as a non-root user and pre-create the mount points it writes to.
RUN useradd --create-home --uid 1000 appuser \
    && mkdir -p /data/adbk /config \
    && chown -R appuser /data/adbk /config /app /opt/venv
USER appuser

VOLUME ["/data/adbk"]
WORKDIR /app

# Default entry point runs the application; the dev service overrides this to
# run pytest / ruff / mypy against a bind-mounted source tree.
ENTRYPOINT ["python", "-m", "adbk"]
CMD ["--help"]
