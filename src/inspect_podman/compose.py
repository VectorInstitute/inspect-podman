"""Podman compose orchestration helpers."""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import re
import time
from logging import getLogger
from pathlib import Path
from typing import Any, Literal, cast

import yaml
from inspect_ai.util import ExecResult, subprocess
from inspect_ai.util import concurrency as concurrency_manager

from .prereqs import resolve_compose_cmd
from .service import ComposeService, service_healthcheck_time, services_healthcheck_time
from .util import ComposeProject

logger = getLogger(__name__)

# How long to wait for compose environment to start if no healthcheck is defined
COMPOSE_WAIT = 120


async def compose_up(
    project: ComposeProject, services: dict[str, ComposeService]
) -> ExecResult[str]:
    """Start compose services for a project."""
    # use a best-effort timeout based on healthchecks
    healthcheck_time = services_healthcheck_time(services)
    timeout = healthcheck_time if healthcheck_time > 0 else COMPOSE_WAIT
    result = await compose_command(
        ["up", "--detach"],
        project=project,
        timeout=timeout,
    )
    return result


async def compose_wait_for_health(
    project: ComposeProject, services: dict[str, ComposeService]
) -> None:
    """Wait for services with healthchecks to become healthy."""
    healthcheck_services = {
        name for name, service in services.items() if service.get("healthcheck")
    }
    delay = _startup_delay()
    services_without_healthcheck = [
        name for name, service in services.items() if not service.get("healthcheck")
    ]
    if not healthcheck_services:
        if delay > 0 and services_without_healthcheck:
            logger.info(
                "Waiting %.1fs for podman services without healthchecks: %s",
                delay,
                ", ".join(sorted(services_without_healthcheck)),
            )
            await asyncio.sleep(delay)
        return

    healthcheck_time = services_healthcheck_time(services)
    timeout = max(healthcheck_time, COMPOSE_WAIT)
    deadline = time.monotonic() + timeout
    healthcheck_services = set(healthcheck_services)

    while True:
        running = await podman_ps(project.name, status="running", all=False)
        container_by_service = {
            container.get("Service"): container.get("Name")
            for container in running
            if container.get("Service") and container.get("Name")
        }

        pending: list[str] = []
        missing_health: set[str] = set()
        for service in list(healthcheck_services):
            container = container_by_service.get(service)
            if not container:
                pending.append(service)
                continue

            status = await _container_health_status(container)
            if status is None:
                missing_health.add(service)
                continue
            if status != "healthy":
                pending.append(service)

        if missing_health:
            fallback_time = max(
                (service_healthcheck_time(services[name]) for name in missing_health),
                default=0,
            )
            delay = max(delay, fallback_time)
            logger.warning(
                "Podman did not report health status for: %s. "
                "Falling back to startup delay.",
                ", ".join(sorted(missing_health)),
            )
            healthcheck_services -= missing_health
            services_without_healthcheck = sorted(
                set(services_without_healthcheck) | missing_health
            )
            if not healthcheck_services:
                break

        if not pending:
            break

        if time.monotonic() >= deadline:
            raise RuntimeError(
                "Timed out waiting for podman healthchecks to pass: "
                + ", ".join(sorted(pending))
            )

        await asyncio.sleep(1)

    if delay > 0 and services_without_healthcheck:
        logger.info(
            "Waiting %.1fs for podman services without healthchecks: %s",
            delay,
            ", ".join(sorted(services_without_healthcheck)),
        )
        await asyncio.sleep(delay)


async def compose_down(project: ComposeProject, quiet: bool = True) -> None:
    """Stop compose services for a project."""
    cwd = os.path.dirname(project.config) if project.config else None
    timeout = 60
    try:
        result = await compose_command(
            ["down", "--volumes"],
            project=project,
            cwd=cwd,
            timeout=timeout,
            capture_output=quiet,
        )
        if not result.success:
            logger.warning(f"Failed to stop podman services: {result.stderr}")
    except TimeoutError:
        logger.warning(
            "Podman compose down timed out after %s seconds for project '%s'.",
            timeout,
            project.name,
        )


async def compose_build(project: ComposeProject, capture_output: bool = False) -> None:
    """Build compose images for a project."""
    result = await compose_command(
        ["build"],
        project=project,
        timeout=None,
        capture_output=capture_output,
    )
    if not result.success:
        raise RuntimeError("Failed to build podman containers")


async def compose_pull(
    service: str, project: ComposeProject, capture_output: bool = False
) -> ExecResult[str]:
    """Pull a service image for a project."""
    return await compose_command(
        ["pull", service],
        project=project,
        timeout=None,
        capture_output=capture_output,
    )


async def compose_services(project: ComposeProject) -> dict[str, ComposeService]:
    """Parse services from the project's compose file."""
    if not project.config:
        raise RuntimeError("Podman compose config file is missing")
    with open(project.config, "r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    services = data.get("services") or {}
    if not isinstance(services, dict):
        raise RuntimeError("Invalid compose file: services must be a mapping")
    return cast(dict[str, ComposeService], services)


async def compose_check_running(
    services: list[str], project: ComposeProject
) -> list[str]:
    """Return running services if all requested services are up."""
    running = await podman_ps(project.name, status="running", all=False)
    exited = await podman_ps(project.name, status="exited", all=True)

    successful_services: set[str] = set()
    running_services: set[str] = set()
    for container in running:
        service = container.get("Service")
        if service:
            running_services.add(service)
            successful_services.add(service)

    for container in exited:
        service = container.get("Service")
        if not service:
            continue
        exit_code = container.get("ExitCode")
        if exit_code == 0:
            successful_services.add(service)

    if not successful_services:
        return []

    if set(services) - successful_services:
        return []

    return list(running_services)


async def compose_cleanup_images(
    project: ComposeProject,
    *,
    cwd: str | None = None,
    timeout: int | None,
) -> None:
    """Cleanup images created for the project."""
    try:
        result = await subprocess(
            ["podman", "images", "--format", "json"],
            timeout=timeout,
            cwd=cwd,
            capture_output=True,
        )
        if not result.success:
            return
        images = json.loads(result.stdout) if result.stdout.strip() else []
        if not isinstance(images, list):
            return
        for image in images:
            repository = image.get("Repository") or ""
            tag = image.get("Tag") or ""
            if repository.startswith(project.name):
                name = f"{repository}:{tag}" if tag else repository
                await subprocess(
                    ["podman", "rmi", name],
                    timeout=timeout,
                    capture_output=True,
                )
    except Exception as ex:
        logger.warning("Failed to cleanup podman images: %s", ex)


async def compose_command(
    command: list[str],
    *,
    project: ComposeProject,
    timeout: int | None,
    timeout_retry: bool = True,
    concurrency: bool = True,
    input: str | bytes | None = None,
    cwd: str | Path | None = None,
    forward_env: bool = True,
    capture_output: bool = True,
    output_limit: int | None = None,
) -> ExecResult[str]:
    """Run a compose command for the given project."""
    compose_cmd = await resolve_compose_cmd()

    env = project.env if (project.env and forward_env) else {}

    cmd = list(compose_cmd)
    cmd.extend(["-p", project.name])
    if project.config:
        cmd.extend(["-f", project.config])
    cmd.extend(command)

    default_cli_concurrency = max((os.cpu_count() or 1) * 2, 4)
    cli_concurrency = int(
        os.environ.get("INSPECT_PODMAN_CLI_CONCURRENCY", default_cli_concurrency)
    )

    async def run_command(command_timeout: int | None) -> ExecResult[str]:
        concurrency_ctx = (
            concurrency_manager("podman-cli", cli_concurrency, visible=False)
            if concurrency
            else contextlib.nullcontext()
        )
        async with concurrency_ctx:
            return await subprocess(
                cmd,
                input=input,
                cwd=cwd,
                env=env,
                timeout=command_timeout,
                capture_output=capture_output,
                output_limit=output_limit,
                concurrency=concurrency,
            )

    if timeout is None:
        return await run_command(timeout)

    retries = 0
    retry_timeouts = [timeout, min(timeout, 60), min(timeout, 30)]
    while True:
        try:
            return await run_command(retry_timeouts[min(retries, 2)])
        except TimeoutError as ex:
            retries += 1
            if not timeout_retry or retries > 2:
                raise ex


async def _container_health_status(container: str) -> str | None:
    result = await subprocess(
        ["podman", "inspect", container, "--format", "{{json .State.Health}}"],
        timeout=10,
        capture_output=True,
    )
    if not result.success:
        return None
    output = result.stdout.strip()
    if not output or output == "null":
        return None
    try:
        payload = json.loads(output)
    except json.JSONDecodeError:
        return None
    if isinstance(payload, dict):
        status = payload.get("Status")
        if isinstance(status, str):
            return status
    return None


def _startup_delay() -> float:
    value = os.environ.get("INSPECT_PODMAN_STARTUP_DELAY", "").strip()
    if not value:
        return 0.0
    try:
        delay = float(value)
    except ValueError:
        logger.warning(
            "Invalid INSPECT_PODMAN_STARTUP_DELAY value '%s'; ignoring.", value
        )
        return 0.0
    return max(delay, 0.0)


async def podman_ps(
    project_name: str,
    status: Literal["running", "exited"] | None = None,
    all: bool = False,
) -> list[dict[str, Any]]:
    """Return podman ps info filtered by compose project and status."""
    args = ["podman", "ps", "--format", "json"]
    if all:
        args.append("--all")

    result = await subprocess(args, timeout=60)
    if not result.success:
        raise RuntimeError(result.stderr)

    output = result.stdout.strip()
    containers = json.loads(output) if output else []
    if not isinstance(containers, list):
        return []
    filtered: list[dict[str, Any]] = []
    for container in containers:
        labels = _normalize_labels(container.get("Labels"))
        project = labels.get("io.podman.compose.project") or labels.get(
            "com.docker.compose.project"
        )
        if project != project_name:
            continue

        service = labels.get("io.podman.compose.service") or labels.get(
            "com.docker.compose.service"
        )

        state = _container_state(container)
        if status == "running" and not _is_running(state):
            continue
        if status == "exited" and not _is_exited(state):
            continue

        filtered.append(
            {
                "Name": _container_name(container),
                "Service": service,
                "State": state,
                "ExitCode": _container_exit_code(container),
            }
        )

    return filtered


def _container_state(container: dict[str, Any]) -> str:
    state = container.get("State") or ""
    status = container.get("Status") or ""
    return str(state or status)


def _is_running(state: str) -> bool:
    lower = state.lower()
    return lower == "running" or lower.startswith("up") or "running" in lower


def _is_exited(state: str) -> bool:
    lower = state.lower()
    return lower == "exited" or "exited" in lower


def _container_exit_code(container: dict[str, Any]) -> int | None:
    exit_code = container.get("ExitCode")
    if isinstance(exit_code, int):
        return exit_code

    status = str(container.get("Status", ""))
    match = re.search(r"exited\s*\((\d+)\)", status, re.IGNORECASE)
    if match:
        return int(match.group(1))

    return None


def _container_name(container: dict[str, Any]) -> str | None:
    names = container.get("Names")
    if isinstance(names, list) and names:
        return names[0]
    if isinstance(names, str):
        return names
    return container.get("Name")


def _normalize_labels(labels: Any) -> dict[str, str]:
    if isinstance(labels, dict):
        return {str(k): str(v) for k, v in labels.items()}
    if isinstance(labels, str):
        parsed: dict[str, str] = {}
        for item in labels.split(","):
            if "=" in item:
                key, value = item.split("=", 1)
                parsed[key.strip()] = value.strip()
        return parsed
    return {}
