"""Wrapper for inspect_evals.gaia.gaia.gaia."""

from inspect_ai import task
from inspect_evals.gaia.gaia import gaia as _task

from inspect_podman.inspect_evals import as_podman


@task
def gaia(**kwargs):
    """Wrapper for inspect-evals task `gaia`."""
    return as_podman(_task(**kwargs))
