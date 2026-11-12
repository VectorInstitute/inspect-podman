"""Cleanup routines for Podman sandbox projects."""

from __future__ import annotations

import json
from contextvars import ContextVar
from dataclasses import dataclass, field
from pathlib import Path
from typing import Awaitable, Callable, Set

import anyio
from inspect_ai._util._async import coro_print_exceptions
from inspect_ai.util import subprocess
from inspect_ai.util._anyio import safe_current_task_id
from inspect_ai.util._sandbox.lifecycle import sandbox_lifecycle_state
from rich import box, print
from rich.panel import Panel
from rich.table import Table

from .compose import compose_down, podman_ps
from .config import is_auto_compose_file, safe_cleanup_auto_compose
from .util import ComposeProject, is_inspect_project


@dataclass
class PodmanCleanupState:
    """The Podman resources one eval batch has started and must clean up.

    As in Inspect's Docker provider, this lives on the batch's sandbox
    lifecycle scope, so a late `task_init` (a `TaskSource` task, or a
    `SampleSource` feeder) registers with the same object as the samples.
    """

    running_projects: list[ComposeProject] = field(default_factory=list)
    """Projects brought up by `sample_init` and not yet brought down."""

    auto_compose_files: set[str] = field(default_factory=set)
    """Generated compose files to remove at shutdown."""

    closed: bool = False
    """`project_cleanup_shutdown` has run: this lifecycle is finished."""

    owner: int | None = None
    """For a registry bound outside a scope, the id of the task that bound it."""


def cleanup_state() -> PodmanCleanupState:
    """Return the cleanup registry of the enclosing sandbox lifecycle scope.

    Outside any scope (the provider driven directly), return the registry
    bound to the current context, binding one if `task_init` never ran here.
    """
    scope = sandbox_lifecycle_state()
    if scope is not None:
        return scope.get(PodmanCleanupState)
    state = _ownerless_state.get()
    if state is None:
        state = _bind_ownerless_state()
    return state


def _bind_ownerless_state() -> PodmanCleanupState:
    state = PodmanCleanupState(owner=safe_current_task_id())
    _ownerless_state.set(state)
    return state


def project_cleanup_startup() -> None:
    """Make sure a cleanup registry exists before samples start.

    Inside a scope the registry is the scope's and is never reset, so a late
    `task_init` keeps everything the batch has already registered. Outside a
    scope, a registry belongs to the task that bound it for one lifecycle:
    another task, or a `task_init` after its shutdown, binds a new one.
    """
    if sandbox_lifecycle_state() is not None:
        cleanup_state()
        return
    state = _ownerless_state.get()
    if state is None or state.closed or state.owner != safe_current_task_id():
        _bind_ownerless_state()


def project_startup(project: ComposeProject) -> None:
    """Register a project as running for later cleanup."""
    running_projects().append(project)
    project_record_auto_compose(project)


def project_record_auto_compose(project: ComposeProject) -> None:
    """Track auto-generated compose files for cleanup."""
    if project.config and is_auto_compose_file(project.config, project.name):
        auto_compose_files().add(project.config)


def project_discard_auto_compose(project: ComposeProject) -> None:
    """Remove the project's generated compose file and stop tracking it."""
    if project.config and is_auto_compose_file(project.config, project.name):
        auto_compose_files().discard(project.config)
        safe_cleanup_auto_compose(project.config)


async def project_cleanup(project: ComposeProject, quiet: bool = True) -> None:
    """Stop and cleanup a project compose stack."""
    await compose_down(project=project, quiet=quiet)
    if project in running_projects():
        running_projects().remove(project)


async def project_cleanup_shutdown(cleanup: bool) -> None:
    """Cleanup all running projects and tracked auto-compose files."""
    if cleanup_state().closed:
        return

    shutdown_projects = running_projects().copy()

    if shutdown_projects:
        if cleanup:
            await cleanup_projects(shutdown_projects)
        else:
            print("")
            table = Table(
                title="Podman Sandbox Environments (not yet cleaned up):",
                box=box.SQUARE_DOUBLE_HEAD,
                show_lines=True,
                title_style="bold",
                title_justify="left",
            )
            table.add_column("Sample ID")
            table.add_column("Epoch")
            table.add_column("Container(s)", no_wrap=True)
            for project in shutdown_projects:
                containers = await podman_ps(project.name, all=True)
                table.add_row(
                    str(project.sample_id) if project.sample_id is not None else "",
                    str(project.epoch if project.epoch is not None else ""),
                    "\n".join(
                        container.get("Name") or ""
                        for container in containers
                        if container.get("Name")
                    ),
                )
            print(table)
            print(
                "\n"
                "Cleanup all containers  : "
                "[blue]inspect sandbox cleanup podman[/blue]\n"
                "Cleanup single container: "
                "[blue]inspect sandbox cleanup podman <container-id>[/blue]",
                "\n",
            )

    for file in auto_compose_files().copy():
        safe_cleanup_auto_compose(file)

    cleanup_state().closed = True


async def cleanup_projects(
    projects: list[ComposeProject],
    cleanup_fn: Callable[[ComposeProject, bool], Awaitable[None]] = project_cleanup,
) -> None:
    """Cleanup a list of compose projects in parallel."""
    print(
        Panel(
            "[bold][blue]Cleaning up Podman environments "
            + "(please do not interrupt this operation!):[/blue][/bold]",
        )
    )

    async with anyio.create_task_group() as tg:
        for project in projects:
            tg.start_soon(
                coro_print_exceptions,
                "cleaning up Podman environment",
                cleanup_fn,
                project,
                False,
            )


async def cli_cleanup(identifier: str | None) -> None:
    """Handle cleanup invoked from the Inspect CLI."""
    containers = await _all_compose_containers()
    if identifier:
        containers = [
            c
            for c in containers
            if c["project"] == identifier
            or c["name"] == identifier
            or ((c["id"] or "").startswith(identifier))
        ]
    else:
        containers = [c for c in containers if is_inspect_project(c["project"])]

    if not containers:
        return

    projects: dict[str, str | None] = {}
    for container in containers:
        project = container["project"]
        config = container["config"]
        if project not in projects:
            projects[project] = config

    for project, config in projects.items():
        if config and Path(config).exists():
            compose_project = await ComposeProject.create(name=project, config=config)
            try:
                await compose_down(compose_project, quiet=False)
            except Exception:
                await _remove_project_containers(project)
        else:
            await _remove_project_containers(project)

    for project in projects:
        for container in containers:
            config = container["config"]
            if (
                container["project"] == project
                and config
                and is_auto_compose_file(config, project)
            ):
                safe_cleanup_auto_compose(config)


async def _all_compose_containers() -> list[dict[str, str | None]]:
    result = await subprocess(["podman", "ps", "--all", "--format", "json"], timeout=60)
    if not result.success:
        raise RuntimeError(result.stderr)

    containers = json.loads(result.stdout) if result.stdout.strip() else []
    if not isinstance(containers, list):
        return []
    discovered: list[dict[str, str | None]] = []
    for container in containers:
        labels = _normalize_labels(container.get("Labels"))
        project = labels.get("io.podman.compose.project") or labels.get(
            "com.docker.compose.project"
        )
        if not project:
            continue

        config = _compose_config_from_labels(labels)
        discovered.append(
            {
                "id": _container_id(container),
                "name": _container_name(container),
                "project": project,
                "config": config,
            }
        )

    return discovered


async def _remove_project_containers(project: str) -> None:
    containers = await podman_ps(project, all=True)
    for container in containers:
        name = container.get("Name")
        if not name:
            continue
        await subprocess(["podman", "rm", "-f", name], timeout=60)


def _compose_config_from_labels(labels: dict[str, str]) -> str | None:
    working_dir = labels.get("com.docker.compose.project.working_dir") or labels.get(
        "io.podman.compose.project.working_dir"
    )
    config_files = labels.get("com.docker.compose.project.config_files") or labels.get(
        "io.podman.compose.project.config_files"
    )
    if not working_dir or not config_files:
        return None

    config_file = config_files.split(",")[0].strip()
    config_path = Path(config_file)
    if not config_path.is_absolute():
        config_path = Path(working_dir) / config_file
    return config_path.as_posix()


def _container_id(container: dict[str, object]) -> str | None:
    container_id = container.get("Id") or container.get("ID")
    if isinstance(container_id, str):
        return container_id
    return None


def _container_name(container: dict[str, object]) -> str | None:
    names = container.get("Names")
    if isinstance(names, list) and names:
        first = names[0]
        if isinstance(first, str):
            return first
    if isinstance(names, str):
        return names
    name = container.get("Name")
    if isinstance(name, str):
        return name
    return None


def _normalize_labels(labels: object) -> dict[str, str]:
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


def running_projects() -> list[ComposeProject]:
    """Return the list of tracked running projects."""
    return cleanup_state().running_projects


def auto_compose_files() -> Set[str]:
    """Return the set of tracked auto-compose files."""
    return cleanup_state().auto_compose_files


_ownerless_state: ContextVar[PodmanCleanupState | None] = ContextVar(
    "podman_cleanup_state", default=None
)
