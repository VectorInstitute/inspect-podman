"""Wrapper for inspect_evals.bigcodebench.bigcodebench.bigcodebench."""

from inspect_ai import task
from inspect_evals.bigcodebench.bigcodebench import bigcodebench as _task

from inspect_podman.inspect_evals import as_podman


@task
def bigcodebench(**kwargs):
    """Wrapper for inspect-evals task `bigcodebench`."""
    return as_podman(_task(**kwargs))
