"""Wrapper for inspect_evals.class_eval.class_eval.class_eval."""

from inspect_ai import task
from inspect_evals.class_eval.class_eval import class_eval as _task

from inspect_podman.inspect_evals import as_podman


@task
def class_eval(**kwargs):
    """Wrapper for inspect-evals task `class_eval`."""
    return as_podman(_task(**kwargs))
