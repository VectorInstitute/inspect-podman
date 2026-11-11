"""Config discovery and auto-compose helpers for Podman sandboxes."""

from __future__ import annotations

import os
from pathlib import Path

COMPOSE_FILES = [
    "compose.yaml",
    "compose.yml",
    "docker-compose.yaml",
    "docker-compose.yml",
]

DOCKERFILE = "Dockerfile"
CONTAINERFILE = "Containerfile"

COMPOSE_COMMENT = """# inspect auto-generated compose file
# (will be removed when its project is cleaned up)"""

COMPOSE_GENERIC_YAML = f"""{COMPOSE_COMMENT}
services:
  default:
    image: "aisiuk/inspect-tool-support"
    command: "tail -f /dev/null"
    init: true
    network_mode: none
    stop_grace_period: 1s
"""

COMPOSE_DOCKERFILE_YAML = f"""{COMPOSE_COMMENT}
services:
  default:
    build:
      context: "."
      dockerfile: "{{dockerfile}}"
    command: "tail -f /dev/null"
    init: true
    network_mode: none
    stop_grace_period: 1s
"""


def find_compose_file(parent: str = "") -> str | None:
    """Return the first matching compose file in a directory."""
    for file in COMPOSE_FILES:
        if os.path.isfile(os.path.join(parent, file)):
            return file
    return None


def has_dockerfile(parent: str = "") -> bool:
    """Return True if a Dockerfile exists in the directory."""
    return os.path.isfile(os.path.join(parent, DOCKERFILE))


def has_containerfile(parent: str = "") -> bool:
    """Return True if a Containerfile exists in the directory."""
    return os.path.isfile(os.path.join(parent, CONTAINERFILE))


def auto_compose_name(project_name: str) -> str:
    """Return the file name of a project's generated compose file."""
    return f".compose-{project_name}.yaml"


def is_auto_compose_file(file: str, project_name: str) -> bool:
    """Return True if the file is the compose file generated for the project."""
    return os.path.basename(file) == auto_compose_name(project_name)


def is_dockerfile(file: str) -> bool:
    """Return True if the file is a Dockerfile or Containerfile."""
    path = Path(file)
    return path.name in {DOCKERFILE, CONTAINERFILE}


def resolve_compose_file(project_name: str, parent: str = "") -> str:
    """Resolve the compose file or synthesize one for the project if needed."""
    compose = find_compose_file(parent)
    if compose is not None:
        return Path(os.path.join(parent, compose)).resolve().as_posix()

    if has_containerfile(parent):
        return auto_compose_file(
            COMPOSE_DOCKERFILE_YAML.format(dockerfile=CONTAINERFILE),
            project_name,
            parent,
        )

    if has_dockerfile(parent):
        return auto_compose_file(
            COMPOSE_DOCKERFILE_YAML.format(dockerfile=DOCKERFILE),
            project_name,
            parent,
        )

    return auto_compose_file(COMPOSE_GENERIC_YAML, project_name, parent)


def safe_cleanup_auto_compose(file: str | None) -> None:
    """Remove a generated compose file if it exists.

    Callers check that the file is their project's with `is_auto_compose_file`.
    """
    if not file:
        return
    try:
        if os.path.exists(file):
            os.unlink(file)
    except Exception:
        pass


def auto_compose_file(contents: str, project_name: str, parent: str = "") -> str:
    """Write the project's generated compose file and return its path.

    The file goes in `parent` so relative paths in it resolve as they would
    for a compose file in that directory.
    """
    path = os.path.join(parent, auto_compose_name(project_name))
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(contents)
    return Path(path).resolve().as_posix()
