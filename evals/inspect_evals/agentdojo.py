"""Wrapper for inspect_evals.agentdojo.agentdojo.agentdojo."""

from inspect_ai import task
from inspect_evals.agentdojo.agentdojo import agentdojo as _task

from inspect_podman.inspect_evals import as_podman


@task
def agentdojo(**kwargs):
    """Wrapper for inspect-evals task `agentdojo`."""
    return as_podman(_task(**kwargs))
