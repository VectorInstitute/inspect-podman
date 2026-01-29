# Inspect Podman Sandbox

A Podman-backed sandbox environment for the [Inspect](https://inspect.aisi.org.uk) evaluation framework. It provides a Podman implementation of Inspect’s sandbox API so evals can run containerized tool calls without Docker.

## Features

- Registers a `podman` sandbox provider via Inspect extension entry points.
- Uses Podman Compose (`podman compose`) or the standalone `podman-compose` tool.
- Supports `compose.yaml`, `Dockerfile`, and `Containerfile` discovery in task folders.
- Provides per-sample container isolation with Inspect’s sandbox lifecycle hooks.

## Start Podman

Make sure Podman and Podman Compose are installed and available on your PATH, then ensure the Podman service is running.

Linux does not require `podman machine` (Podman runs natively). If you are on macOS/Windows, start the Podman VM:

```
podman machine init
podman machine start
```

Verify connectivity:

```
podman info
```

Check the connection:

```
podman system connection list
podman info
```

## Install

From this repo:

```
uv sync
```

For editable installs:

```
uv pip install -e .
```

Activate the environment:

```
source .venv/bin/activate
```

Some evals and model providers require extra Python packages (e.g., `openai`). Since this project uses `uv`, install any optional dependencies with `uv` as well. We don’t include these by default to keep the extension lightweight.

Example:

```
uv pip install openai
```

If you don’t use `uv`, you can install with `pip` instead:

```
python -m venv .venv
source .venv/bin/activate
pip install -e .
```

## Usage with Inspect

In Python:

```python
from inspect_ai import eval

eval("my_task.py", sandbox="podman")
```

From the CLI:

```
inspect eval my_task.py --sandbox podman
```

To specify a custom compose file:

```python
eval("my_task.py", sandbox=("podman", "compose.yaml"))
```

## Evals in This Repo

Sample evals live in [evals/](evals/) and are for testing the Podman provider. Start with:

```
inspect eval evals/file_listing/file_listing.py
```

If you’re writing your own evals, set the sandbox to `podman` (e.g., `sandbox="podman"` or `sandbox=("podman", "compose.yaml")`) to run them with Podman instead of Docker.

See [docs/evals-usage.md](docs/evals-usage.md) for details, including how to use this extension with [inspect_evals](https://github.com/UKGovernmentBEIS/inspect_evals).

## Forcing podman-compose (optional)

By default we auto-detect the compose frontend. If you need to force the
standalone `podman-compose` binary (e.g., to match an environment that only
has `podman-compose` available), set:

```
export INSPECT_PODMAN_COMPOSE=podman-compose
```

## Compose and Dockerfile Discovery

The provider searches the task directory in this order:

1. `compose.yaml`, `compose.yml`, `docker-compose.yaml`, `docker-compose.yml`
2. `.compose.yaml` (auto-generated)
3. `Containerfile`
4. `Dockerfile`
5. A default compose that uses the `aisiuk/inspect-tool-support` image

If you provide `sandbox=("podman", "path/to/compose.yaml")`, that file is used directly.

## Healthchecks

If a compose service defines a healthcheck, the provider waits for it to report `healthy` before running the sample. This mirrors how Inspect handles readiness in Docker-based sandboxes and is exercised by `evals/file_listing_healthcheck/`.

You can add a fixed startup delay (for services without healthchecks or when Podman doesn’t report health status) by setting:

```
export INSPECT_PODMAN_STARTUP_DELAY=5
```

This is useful when a service is ready shortly after startup but doesn’t expose a healthcheck, or when Podman does not surface health status for a container. Otherwise the eval may start too early and fail.

## Cleanup

Inspect will clean up pods/containers automatically unless you disable it:

```
inspect eval my_task.py --no-sandbox-cleanup
```

Manual cleanup:

```
inspect sandbox cleanup podman
inspect sandbox cleanup podman <container-id>
```

## Notes and Limitations

- Compose services that set `container_name` are rejected because Inspect runs multiple epochs/samples and needs unique container names per run; fixed names would collide across runs.
- This extension is intended as a Docker replacement for Inspect sandboxing, but behavior can differ across Podman versions and compose features. If an eval relies on Docker‑specific behavior, you may need small adjustments.
