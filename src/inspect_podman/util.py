"""Project helpers for Podman compose sandboxes."""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from pathlib import Path

import yaml
from inspect_ai.util import ComposeConfig

from .config import (
    COMPOSE_DOCKERFILE_YAML,
    auto_compose_file,
    ensure_auto_compose_file,
    is_dockerfile,
    resolve_compose_file,
)


@dataclass
class ComposeProject:
    """Represents a compose project for a sandbox run."""

    name: str
    config: str | None
    sample_id: int | str | None
    epoch: int | None
    env: dict[str, str] | None

    @classmethod
    async def create(
        cls,
        name: str,
        config: str | ComposeConfig | None,
        *,
        sample_id: int | str | None = None,
        epoch: int | None = None,
        env: dict[str, str] | None = None,
    ) -> "ComposeProject":
        """Create a project with resolved compose config."""
        config_path: Path | None = None
        if isinstance(config, str):
            config_path = Path(config).resolve()
        elif isinstance(config, ComposeConfig):
            config_yaml = yaml.dump(
                config.model_dump(mode="json", by_alias=True, exclude_none=True),
                default_flow_style=False,
                sort_keys=False,
            )
            config = auto_compose_file(config_yaml)
        elif config is not None:
            raise ValueError(
                "Unsupported config type: "
                f"{type(config)}. Expected str or ComposeConfig."
            )

        if config_path and is_dockerfile(config_path.name):
            config = auto_compose_file(
                COMPOSE_DOCKERFILE_YAML.format(dockerfile=config_path.name),
                config_path.parent.as_posix(),
            )
        elif config_path:
            config = config_path.as_posix()
        elif config is None:
            config = resolve_compose_file()

        ensure_auto_compose_file(config)

        return ComposeProject(
            name=name,
            config=config,
            sample_id=sample_id,
            epoch=epoch,
            env=env or {},
        )

    def __eq__(self, other: object) -> bool:
        """Compare projects by name."""
        if not isinstance(other, ComposeProject):
            return NotImplemented
        return self.name == other.name


def task_project_name(task: str) -> str:
    """Return a normalized compose project name for a task."""
    task = task.lower()
    task = re.sub(r"[^a-z\d\-_]", "-", task)
    task = re.sub(r"-+", "-", task)
    if not task:
        task = "task"

    return f"inspect-{task[:12].rstrip('_')}-i{uuid.uuid4().hex[:6]}"


inspect_project_pattern = r"^inspect-[a-z\d\-_]*-i[a-z\d]{6,}$"


def is_inspect_project(name: str) -> bool:
    """Return True if the project name matches Inspect's pattern."""
    return re.match(inspect_project_pattern, name) is not None
