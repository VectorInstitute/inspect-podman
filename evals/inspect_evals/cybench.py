"""Wrapper for inspect_evals.cybench.cybench.cybench."""

from inspect_ai import task
from inspect_evals.cybench.cybench import cybench as _task

from inspect_podman.inspect_evals import as_podman


@task
def cybench(**kwargs):
    """Wrapper for inspect-evals task `cybench`."""
    return as_podman(_task(**kwargs))
