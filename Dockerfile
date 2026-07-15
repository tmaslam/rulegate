# syntax=docker/dockerfile:1.9
# ---------------------------------------------------------------------------
# policy-guarded-ops-agent — multi-stage build: uv builder -> slim runtime, non-root.
#
# Placeholders: policy-guarded-ops-agent, policy_guarded_ops_agent
#
# NOTE FOR THE AUTHOR: this was written and reviewed by inspection — Docker is
# not installed on the dev machine. CI (`docker/build-push-action`) is the first
# place it actually builds, and that is deliberate: the GitHub Actions runner is
# free on public repos. Do not add a local `docker build` step to any Makefile
# target that must work offline.
#
# Build:  docker build -t policy-guarded-ops-agent .
# Run:    docker run --rm -p 8000:8000 --env-file .env policy-guarded-ops-agent
#
# Runs with NO env file too: no keys => deterministic fake provider, tracing
# no-ops, SQLite fallback. That is the zero-account path and it is supported.
# ---------------------------------------------------------------------------

# --- Stage 1: builder ------------------------------------------------------
# Pinned to a uv minor and a Python patch. `latest` in a Dockerfile is how a
# build that worked in June breaks in July.
FROM ghcr.io/astral-sh/uv:0.5-python3.12-bookworm-slim AS builder

# UV_COMPILE_BYTECODE: precompile .pyc at build time so container start does not
#   pay for it (matters on a cold Render/HF Spaces free-tier boot).
# UV_LINK_MODE=copy: the cache mount is a different filesystem; hardlinking
#   across it warns and falls back. Copying is explicit and silent.
# UV_PYTHON_DOWNLOADS=never: use the interpreter already in the image; never
#   fetch one mid-build.
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app

# Dependency layer, resolved from the lockfile ONLY. Bind-mounting the manifests
# instead of COPYing them keeps this layer keyed on the lockfile alone, so it
# stays cached across every source edit. `--no-install-project` is what makes
# that separation real: the project itself is installed in the next layer.
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --locked --no-install-project --no-dev

# Now the source. Changing a .py file invalidates only from here down.
COPY . /app

# Install the project itself into the same venv.
# --no-dev: ruff/mypy/pytest have no business in a runtime image.
# --locked: fail loudly if uv.lock disagrees with pyproject.toml, rather than
#   silently resolving something the tests never saw.
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev

# --- Stage 2: runtime ------------------------------------------------------
# Plain python:3.12-slim, NOT the uv image: uv is a build tool and shipping it
# only widens the attack surface. Same Debian base and same Python minor as the
# builder, so the venv's absolute paths and compiled bytecode stay valid.
FROM python:3.12-slim-bookworm AS runtime

# PYTHONUNBUFFERED: logs reach the platform's collector immediately.
# PYTHONDONTWRITEBYTECODE: bytecode is already baked in; the runtime FS is
#   read-only-ish and non-root cannot write .pyc next to the source anyway.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONFAULTHANDLER=1 \
    PATH="/app/.venv/bin:$PATH"

# Non-root, no shell, no home. A compromised process should not get a login.
# Fixed uid/gid so bind-mounted volumes have predictable ownership.
RUN groupadd --system --gid 1001 app \
    && useradd --system --uid 1001 --gid app --no-create-home --shell /usr/sbin/nologin app

WORKDIR /app

# Copy the built venv and source, already owned by the runtime user. Doing the
# chown in COPY avoids a second full-size layer from a later `RUN chown -R`.
COPY --from=builder --chown=app:app /app /app

USER app

EXPOSE 8000

# Liveness only — deliberately does NOT call an LLM provider. A health check that
# hits a rate-limited free tier will report the container unhealthy the moment
# the quota is hit, and burn the quota to do it.
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2).status==200 else 1)"

# Exec form: uvicorn becomes PID 1 and receives SIGTERM directly, so the platform
# can drain connections instead of SIGKILLing after the grace period.
# Single worker: free tiers give a fraction of a core, and the workload is
# I/O-bound on the provider — async concurrency beats process count here.
CMD ["uvicorn", "policy_guarded_ops_agent.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
