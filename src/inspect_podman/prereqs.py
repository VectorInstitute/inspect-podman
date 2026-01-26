"""Prerequisite validation for Podman sandboxes."""

from __future__ import annotations

import os
import shlex
from logging import getLogger

from inspect_ai._util.error import PrerequisiteError
from inspect_ai.util import subprocess

logger = getLogger(__name__)

_COMPOSE_CMD: list[str] | None = None


async def validate_prereqs() -> None:
    """Validate Podman and compose prerequisites."""
    await validate_podman()
    await resolve_compose_cmd()


async def validate_podman() -> None:
    """Validate that Podman is installed and runnable."""
    try:
        result = await subprocess(["podman", "version", "--format", "json"], timeout=10)
    except FileNotFoundError as ex:
        raise PrerequisiteError(
            "ERROR: Podman sandbox environments require Podman\n\n"
            + "Install: https://podman.io/docs/installation"
        ) from ex

    if not result.success:
        raise PrerequisiteError(
            "ERROR: Podman sandbox environments require a working Podman install\n\n"
            + "podman exited with return code "
            + f"{result.returncode} when executing: "
            + f"{shlex.join(['podman', 'version', '--format', 'json'])}\n"
            + result.stderr
        )


async def resolve_compose_cmd() -> list[str]:
    """Resolve whether to use podman compose or podman-compose."""
    global _COMPOSE_CMD
    if _COMPOSE_CMD is not None:
        return _COMPOSE_CMD

    override = os.environ.get("INSPECT_PODMAN_COMPOSE", "").strip().lower()
    if override:
        if override in {"podman-compose", "podman_compose"}:
            result = await subprocess(["podman-compose", "version"], timeout=10)
            if result.success:
                _COMPOSE_CMD = ["podman-compose"]
                return _COMPOSE_CMD
            raise PrerequisiteError(
                "ERROR: INSPECT_PODMAN_COMPOSE=podman-compose was set but "
                "podman-compose is not available."
            )
        if override in {"podman", "podman compose", "podman-compose:off"}:
            result = await subprocess(["podman", "compose", "version"], timeout=10)
            if result.success:
                _COMPOSE_CMD = ["podman", "compose"]
                return _COMPOSE_CMD
            raise PrerequisiteError(
                "ERROR: INSPECT_PODMAN_COMPOSE=podman was set but "
                "podman compose is not available."
            )

    result = await subprocess(["podman", "compose", "version"], timeout=10)
    if result.success:
        _COMPOSE_CMD = ["podman", "compose"]
        return _COMPOSE_CMD

    result = await subprocess(["podman-compose", "version"], timeout=10)
    if result.success:
        _COMPOSE_CMD = ["podman-compose"]
        return _COMPOSE_CMD

    raise PrerequisiteError(
        "ERROR: Podman sandbox environments require podman compose support\n\n"
        + "Install podman-compose or update Podman to a version that includes "
        + "'podman compose'.\n"
        + "Docs: https://github.com/containers/podman-compose"
    )
