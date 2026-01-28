"""Wrapper for inspect_evals.arc.arc_easy."""

from inspect_ai import task
from inspect_evals.arc import arc_easy as _task

from inspect_podman.inspect_evals import as_podman


@task
def arc_easy(**kwargs):
    """Wrapper for inspect-evals task `arc_easy`."""
    return as_podman(_task(**kwargs))
