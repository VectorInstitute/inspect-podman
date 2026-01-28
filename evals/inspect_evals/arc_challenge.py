"""Wrapper for inspect_evals.arc.arc_challenge."""

from inspect_ai import task
from inspect_evals.arc import arc_challenge as _task

from inspect_podman.inspect_evals import as_podman


@task
def arc_challenge(**kwargs):
    """Wrapper for inspect-evals task `arc_challenge`."""
    return as_podman(_task(**kwargs))
