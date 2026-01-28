"""Wrapper for inspect_evals.gdm_capabilities.in_house_ctf.task.gdm_in_house_ctf."""

from inspect_ai import task
from inspect_evals.gdm_capabilities.in_house_ctf.task import gdm_in_house_ctf as _task

from inspect_podman.inspect_evals import as_podman


@task
def gdm_in_house_ctf(**kwargs):
    """Wrapper for inspect-evals task `gdm_in_house_ctf`."""
    return as_podman(_task(**kwargs))
