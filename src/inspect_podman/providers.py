"""Entry points for Inspect AI sandbox registration."""

from inspect_ai.util import sandboxenv


@sandboxenv(name="podman")
def podman():
    """Register the Podman sandbox environment lazily."""
    from .podman import PodmanSandboxEnvironment

    return PodmanSandboxEnvironment
