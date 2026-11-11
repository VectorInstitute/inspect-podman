"""Config discovery and auto-compose helpers for Podman sandboxes."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

from inspect_ai._util.appdirs import inspect_data_dir
from inspect_ai.util._sandbox.docker.config import _update_build_context

COMPOSE_FILES = [
    "compose.yaml",
    "compose.yml",
    "docker-compose.yaml",
    "docker-compose.yml",
]

DOCKERFILE = "Dockerfile"
CONTAINERFILE = "Containerfile"

AUTO_COMPOSE_SUBDIR = "podman-compose"

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


def auto_compose_dir() -> Path:
    """Return the directory that holds generated compose files."""
    return inspect_data_dir(AUTO_COMPOSE_SUBDIR)


# compose project names: lowercase letters, digits, dashes and underscores
_PROJECT_NAME = re.compile(r"[a-z0-9][a-z0-9_-]*")


def auto_compose_path(project_name: str) -> Path:
    """Return the path of a project's generated compose file."""
    if not _PROJECT_NAME.fullmatch(project_name):
        raise ValueError(f"Invalid compose project name: {project_name!r}")
    compose_dir = auto_compose_dir().resolve()
    path = (compose_dir / f"{project_name}.yaml").resolve()
    if path.parent != compose_dir:
        raise ValueError(f"Invalid compose project name: {project_name!r}")
    return path


def is_auto_compose_file(file: str, project_name: str) -> bool:
    """Return True if the file is the compose file generated for the project.

    A project name that is not a valid compose project name (from a container
    label, say) never matches, so it cannot point outside the directory.
    """
    try:
        path = auto_compose_path(project_name)
    except ValueError:
        return False
    return Path(file).resolve() == path


_REMOTE_CONTEXT = re.compile(r"^(https?|git|ssh)://|^git@", re.IGNORECASE)


def _escape_dollars(path: str) -> str:
    """Escape `$` in a path written into a compose file.

    Compose reads `$` as the start of a variable; `$$` is a literal `$`.
    """
    return path.replace("$", "$$")


def prepare_inline_build_contexts(config: dict[str, Any]) -> None:
    """Check inline build contexts and expand home-relative ones in place.

    Relative build contexts in a generated file are resolved against a base
    directory. Remote contexts and contexts with variables would be broken
    by that, so they must come from a compose file instead. A context that
    starts with `~` is expanded to the user's home directory first, as
    Compose does.

    Raises:
        ValueError: A service's build context is remote or uses a variable.
    """
    for name, service in (config.get("services") or {}).items():
        build = service.get("build") if isinstance(service, dict) else None
        context = build.get("context") if isinstance(build, dict) else build
        if not isinstance(context, str):
            continue
        if _REMOTE_CONTEXT.match(context):
            raise ValueError(
                f"Service '{name}' has a remote build context ({context}). "
                "Remote build contexts are not supported in an inline "
                "ComposeConfig. Use a compose file instead."
            )
        if "$" in context:
            raise ValueError(
                f"Service '{name}' has a variable in its build context "
                f"({context}). Variables in build contexts are not supported "
                "in an inline ComposeConfig. Use a compose file instead."
            )
        if context.startswith("~"):
            expanded = _escape_dollars(os.path.expanduser(context))
            if isinstance(build, dict):
                build["context"] = expanded
            else:
                service["build"] = expanded


def is_dockerfile(file: str) -> bool:
    """Return True if the file is a Dockerfile or Containerfile."""
    path = Path(file)
    return path.name in {DOCKERFILE, CONTAINERFILE}


def resolve_compose_file(project_name: str, parent: str = "") -> str:
    """Resolve the compose file or synthesize one for the project if needed."""
    compose = find_compose_file(parent)
    if compose is not None:
        return Path(os.path.join(parent, compose)).resolve().as_posix()

    base_dir = Path(parent).resolve().as_posix() if parent else os.getcwd()

    if has_containerfile(parent):
        return auto_compose_file(
            COMPOSE_DOCKERFILE_YAML.format(dockerfile=CONTAINERFILE),
            project_name,
            base_dir=base_dir,
        )

    if has_dockerfile(parent):
        return auto_compose_file(
            COMPOSE_DOCKERFILE_YAML.format(dockerfile=DOCKERFILE),
            project_name,
            base_dir=base_dir,
        )

    return auto_compose_file(COMPOSE_GENERIC_YAML, project_name)


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


def auto_compose_file(
    contents: str, project_name: str, base_dir: str | None = None
) -> str:
    """Write the project's generated compose file and return its path.

    The file goes in the central auto-compose directory, outside any build
    context. As in Inspect's Docker provider, relative `build.context` paths
    are resolved against `base_dir`.
    """
    if base_dir:
        contents = _update_build_context(contents, _escape_dollars(base_dir))

    path = auto_compose_path(project_name)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(contents)
    return path.as_posix()
