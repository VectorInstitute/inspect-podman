# Evals in This Repo

This folder contains evals you can use to validate the Podman sandbox provider. These are intended as quick tests and examples, not full benchmarks.

## Evals Included Here

- `file_listing/` — Inspect sandbox example adapted for Podman (single-container sanity check).
- `file_listing_healthcheck/` — Inspect sandbox example with healthcheck support (verifies healthcheck polling/timeout behavior).
- `inspect_evals/` — small set of wrappers for `inspect-evals` tasks covering key Podman scenarios.

## file_listing

[file_listing](file_listing/) is the Inspect sandbox tutorial example adapted to `sandbox="podman"` for a minimal single-container check (see https://inspect.aisi.org.uk/sandboxing.html#example-file-listing). It uses the default `aisiuk/inspect-tool-support` image, which is the standard Inspect sandbox image when no custom compose/Dockerfile is provided.

To run:

```sh
inspect eval evals/file_listing/file_listing.py
```

## file_listing_healthcheck

[file_listing_healthcheck](file_listing_healthcheck/) uses the same task as `file_listing`, but runs via a compose file (see [compose.yaml](file_listing_healthcheck/compose.yaml)) that adds a healthcheck and an explicit container command. The eval explicitly sets `sandbox=("podman", "compose.yaml")` so Inspect uses that compose file.

Why this exists: the Inspect Evals examples don’t include a healthcheck scenario, so we added a minimal one to exercise healthcheck polling/timeouts.

To run:

```sh
inspect eval evals/file_listing_healthcheck/file_listing_healthcheck.py
```

## inspect_evals

[inspect_evals](inspect_evals/) contains a small set of wrappers around `inspect-evals` tasks (see [inspect-evals](https://github.com/UKGovernmentBEIS/inspect_evals)) that cover key Podman scenarios. These wrappers rewrite `sandbox="docker"` to `sandbox="podman"` so the evals can be run with Podman instead of Docker.

Install `inspect-evals` in the same environment first:

```sh
uv pip install inspect-evals
```

Get access to ` https://huggingface.co/datasets/gaia-benchmark/GAIA`.

Below is the list of wrappers we include here:

- `gaia.py` — compose, tool-heavy
- `class_eval.py` — Dockerfile-only build path
- `agentdojo.py` — multi-container compose
- `cybench.py` — multi-container compose
- `gdm_in_house_ctf.py` — multi-container compose
- `arc_easy.py` — model-only sanity
- `arc_challenge.py` — model-only sanity

To run any wrapper, replace the file name as needed:

```sh
inspect eval evals/inspect_evals/gaia.py --limit 1
```

We only include a small set of wrappers for testing. If you clone the upstream `inspect_evals` repo (or use a private fork) and want to run other evals, you can:

1) Pass the sandbox override on the CLI:
```sh
inspect eval path/to/task.py --sandbox podman
```

2) Edit the task and change `sandbox="docker"` to `sandbox="podman"`.

### Notes

- Some evals (e.g., `cybench`) require extra datasets or dependencies; check the [inspect-evals](https://github.com/UKGovernmentBEIS/inspect_evals) docs/README and each eval’s specific requirements before running.
- Some evals may still hardcode `docker` CLI calls internally (for example, image build/pull helpers). In those cases, changing `sandbox="podman"` is not enough; the eval code itself needs to be updated to use Podman or sandbox-agnostic helpers.
