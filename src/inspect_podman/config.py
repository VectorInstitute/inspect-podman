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

AUTO_COMPOSE_YAML = ".compose.yaml"

COMPOSE_COMMENT = """# inspect auto-generated compose file
# (will be removed when task is complete)"""

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


def has_auto_compose_file(parent: str = "") -> bool:
    """Return True if the auto-compose file exists in the directory."""
    return os.path.isfile(os.path.join(parent, AUTO_COMPOSE_YAML))


def is_auto_compose_file(file: str) -> bool:
    """Return True if the file is the auto-compose file."""
    return os.path.basename(file) == AUTO_COMPOSE_YAML


def is_dockerfile(file: str) -> bool:
    """Return True if the file is a Dockerfile or Containerfile."""
    path = Path(file)
    return path.name in {DOCKERFILE, CONTAINERFILE}


def resolve_compose_file(parent: str = "") -> str:
    """Resolve the compose file or synthesize one if needed."""
    compose = find_compose_file(parent)
    if compose is not None:
        return Path(os.path.join(parent, compose)).resolve().as_posix()

    if has_auto_compose_file(parent):
        return Path(os.path.join(parent, AUTO_COMPOSE_YAML)).resolve().as_posix()

    if has_containerfile(parent):
        return auto_compose_file(
            COMPOSE_DOCKERFILE_YAML.format(dockerfile=CONTAINERFILE), parent
        )

    if has_dockerfile(parent):
        return auto_compose_file(
            COMPOSE_DOCKERFILE_YAML.format(dockerfile=DOCKERFILE), parent
        )

    return auto_compose_file(COMPOSE_GENERIC_YAML, parent)


def ensure_auto_compose_file(file: str | None) -> None:
    """Ensure auto-compose file is present if referenced."""
    if file is not None and is_auto_compose_file(file) and not os.path.exists(file):
        resolve_compose_file(os.path.dirname(file))


def safe_cleanup_auto_compose(file: str | None) -> None:
    """Remove auto-compose file if it exists."""
    if not file:
        return
    try:
        if is_auto_compose_file(file) and os.path.exists(file):
            os.unlink(file)
    except Exception:
        pass


def auto_compose_file(contents: str, parent: str = "") -> str:
    """Write and return the auto-compose file path."""
    path = os.path.join(parent, AUTO_COMPOSE_YAML)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(contents)
    return Path(path).resolve().as_posix()
