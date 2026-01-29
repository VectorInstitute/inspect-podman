# GUIDE

## Instructions

Hello @channel, below are the steps for the testing guide:

- Make sure you have access to the repo: https://github.com/VectorInstitute/inspect-podman
- Setup (follow `README.md`)
  - Start Podman and confirm connectivity: `podman info`
  - Set up the Python environment (`README.md`)
- Setup eval config + logging
- I keep eval settings and logs in a `.env` so runs are consistent.\
  Place `.env` in the project root.\
  I have attached my `.env` with this message as an example.\
  See the Inspect docs for more info (https://inspect.aisi.org.uk/options.html https://inspect.aisi.org.uk/eval-logs.html).\
  Make sure you have:
  - Provider credentials (e.g., `OPENAI_API_KEY`, `HF_TOKEN` if needed)
  - Model selection (e.g., `INSPECT_EVAL_MODEL`)
  - Limits (e.g., `INSPECT_EVAL_LIMIT`, `INSPECT_EVAL_TIME_LIMIT`, `INSPECT_EVAL_WORKING_LIMIT`) — we’re not trying to finish the evals here, just validate sandboxing and tool use.
  - Logging/traces (`INSPECT_LOG_LEVEL`, `INSPECT_LOG_FORMAT`, `INSPECT_LOG_DIR`)
- Start testing with the following order: (See `evals/README.md` for details on each of the test evals)
  - Single-container sanity check: `inspect eval evals/file_listing/file_listing.py`
  - Healthcheck validation `inspect eval evals/file_listing_healthcheck/file_listing_healthcheck.py`
  - Inspect Evals wrappers:
    - Install inspect-evals: `uv pip install inspect-evals`
    - `inspect eval --limit=1 evals/inspect_evals/gaia.py`
    - `inspect eval --limit=1 evals/inspect_evals/class_eval.py`
    - `inspect eval --limit=1 evals/inspect_evals/arc_easy.py`
    - `inspect eval --limit=1 evals/inspect_evals/gdm_in_house_ctf.py`
- How to tell if a run succeeded or failed:
  - Success: the eval completes and prints a summary block (no traceback).
    Logs are written to the logs/ directory.
  - Failure: you will see a traceback or a clear error message, and the eval will stop early.
  - If `trace` logging is enabled, you should see `podman ...` subprocess lines in the console output (e.g., `podman compose ...` and `podman exec ...`), which confirms the sandbox is running via Podman.
  - Tool errors: ToolEvent `error` = tool call failure (e.g., browser/network).
    Since the goal is to validate Podman, treat this as an application/tool failure unless you also see Podman/compose errors in the trace.
  - You can keep containers for inspection by adding `--no-sandbox-cleanup` to an eval run.
  - Check status with `podman ps -a` and `podman info`.
  - Clean up when done: inspect sandbox cleanup podman (should complete without errors).

Notes:

- Some evals (e.g., `cybench`) require extra datasets/deps.
- You can add `INSPECT_PODMAN_STARTUP_DELAY=10` when we don't have healthcheck (which is the case for almost all of the evals except the `file_listing_healthcheck` to make sure services are all up and running when the evals start.

## `.env` Template

```
OPENAI_API_KEY=

HF_TOKEN=

INSPECT_EVAL_MODEL="openai/gpt-4o-mini"

INSPECT_LOG_DIR=./logs
INSPECT_LOG_LEVEL=trace
INSPECT_LOG_FORMAT=json

INSPECT_EVAL_LIMIT=1
INSPECT_EVAL_TIME_LIMIT=60
INSPECT_EVAL_WORKING_LIMIT=60
INSPECT_EVAL_MAX_SAMPLES=1
INSPECT_EVAL_MAX_TASKS=1
```

[Open-source framework for large language model evaluations](https://inspect.aisi.org.uk/options.html)

## Extra steps

The following steps were provided by Farnaz after Samuel encountered some issues running `inspect eval evals/file_listing/file_listing.py`.

```sh
podman compose version
podman-compose --version
podman-compose -f evals/file_listing_healthcheck/compose.yaml up -d
podman-compose -f evals/file_listing_healthcheck/compose.yaml logs --tail=200
podman ps -a --format "{{.ID}} {{.Names}} {{.Image}} {{.Status}}"
```

For `cybench`, install its dependency.

```sh
uv pip install inspect-cyber
```

## Success

### gaia

```sh
inspect eval --limit=1 evals/inspect_evals/gaia.py
```

```
total time:                                    0:01:12
openai/gpt-4o-mini                             10,970 tokens [I: 10,885, CW: 0, CR: 3,200, O: 85, R: 0]

gaia_scorer
accuracy     0.000
stderr       0.000
```

### `class_eval`

```sh
inspect eval --limit=1 evals/inspect_evals/class_eval.py
```

```
total time:                                         0:00:21
openai/gpt-4o-mini                                  1,232 tokens [I: 496, CW: 0, CR: 0, O: 736, R: 0]

class_eval_scorer
mean               1.000
std                0.000
```

### `arc_easy`

```sh
inspect eval --limit=1 evals/inspect_evals/arc_easy.py
```

```
total time:                                            0:00:07
openai/gpt-4o-mini                                     113 tokens [I: 110, CW: 0, CR: 0, O: 3, R: 0]

choice
accuracy  1.000
stderr    0.000
```

### `arc_challenge`

```sh
inspect eval --limit=1 evals/inspect_evals/arc_challenge.py
```

```
total time:                                            0:00:01
openai/gpt-4o-mini                                     112 tokens [I: 109, CW: 0, CR: 0, O: 3, R: 0]

choice
accuracy  1.000
stderr    0.000
```

### `agentdojo`

```sh
inspect eval --limit=1 evals/inspect_evals/agentdojo.py
```

```
ImportError: email-validator is not installed, run `pip install 'pydantic[email]'`
```

Fix

```sh
uv pip install 'pydantic[email]'
```

```
total time:                                     0:00:07
openai/gpt-4o-mini                              5,696 tokens [I: 5,515, CW: 0, CR: 2,560, O: 181, R: 0]

injection_task_scorer    utility          security                                                                                                                                                                     accuracy  0.000
accuracy  1.000                                                                                                                                                              stderr    0.000
stderr    0.000
```

## Errors

### `bigcodebench`

```sh
inspect eval evals/inspect_evals/bigcodebench.py --limit 1
```

```
FileNotFoundError: [Errno 2] No such file or directory: 'docker'
```

About bigcodebench, this one isn’t a Podman extension issue.
The bigcodebench eval itself explicitly calls Docker helpers (force_build_or_pull_docker_image) before it runs, which shells out to the docker CLI.
On my machine it works because I have a docker command available; on your system it fails because docker isn’t on PATH, so it throws `FileNotFoundError: 'docker'`.
Even if we set `sandbox="podman"`, that only changes Inspect’s sandbox; it doesn’t override hard‑coded Docker usage inside the eval.

If someone wants to make bigcodebench Podman compatible, they’d need to change the eval’s Docker specific image handling to use Podman or skip it entirely in favor of Inspect’s Podman sandbox path.
For now I’m going to remove this wrapper from our repo, since we can validate Podman functionality with other evals here without requiring a Docker CLI. Let me know what you think :slightly_smiling_face:

If an eval hard-codes the docker CLI (instead of using Inspect’s sandbox API), it won’t work on a Podman only system.
Those direct calls need to be updated to use Podman (or made sandbox‑agnostic) before it’s compatible.

### `cybench`

```sh
env CYBENCH_ACKNOWLEDGE_RISKS=1 inspect eval --limit=1 evals/inspect_evals/cybench.py
```

If you get the following error message you need to `uv pip install inspect-cyber`.

```
ModuleNotFoundError: No module named 'inspect_cyber'
```

Current error message

```
RuntimeError: No services started for podman sandbox

Task interrupted (no samples completed before interruption)
```

### `gdm_in_house_ctf`

```sh
inspect eval --limit=1 evals/inspect_evals/gdm_in_house_ctf.py
```

Farnaz Kohankhaki:

```sh
inspect eval --limit=1 evals/inspect_evals/gdm_in_house_ctf.py --no-sandbox-cleanup --log-level trace
```

```
RuntimeError: No services started for podman sandbox
```
