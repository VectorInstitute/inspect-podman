import json
import os
from pathlib import Path
from typing import Any

import anyio
import pytest
import yaml
from inspect_ai.util import ComposeBuild, ComposeConfig, ComposeService, ExecResult

from inspect_podman import config as podman_config
from inspect_podman import podman
from inspect_podman.cleanup import (
    auto_compose_files,
    cli_cleanup,
    project_cleanup,
    project_cleanup_shutdown,
    project_cleanup_startup,
    project_startup,
)
from inspect_podman.podman import PodmanSandboxEnvironment
from inspect_podman.util import ComposeProject, task_project_name

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    # the provider calls asyncio directly, so it runs under asyncio only
    return "asyncio"


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    """The central auto-compose directory the tests use."""
    path = tmp_path / "data"
    path.mkdir()
    return path


@pytest.fixture
def work_dir(tmp_path: Path) -> Path:
    """The task directory: the working directory of every test."""
    path = tmp_path / "work"
    path.mkdir()
    return path.resolve()


@pytest.fixture(autouse=True)
def no_podman(
    monkeypatch: pytest.MonkeyPatch, data_dir: Path, work_dir: Path
) -> list[str]:
    """Run in an empty task directory with every Podman call stubbed out.

    Returns the compose files `compose_build` was called with.
    """
    monkeypatch.chdir(work_dir)
    monkeypatch.setattr(podman_config, "auto_compose_dir", lambda: data_dir)
    built: list[str] = []

    async def noop(*args: Any, **kwargs: Any) -> None:
        return None

    async def build(project: ComposeProject, *args: Any, **kwargs: Any) -> None:
        assert project.config is not None
        built.append(project.config)

    async def pull(*args: Any, **kwargs: Any) -> ExecResult[str]:
        return ExecResult(success=True, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(podman, "validate_prereqs", noop)
    monkeypatch.setattr(podman, "compose_build", build)
    monkeypatch.setattr(podman, "compose_cleanup_images", noop)
    monkeypatch.setattr(podman, "compose_pull", pull)
    monkeypatch.setattr("inspect_podman.cleanup.compose_down", noop)
    return built


def inline_config(service: str, context: str = "ctx") -> ComposeConfig:
    return ComposeConfig(
        services={
            service: ComposeService(image="alpine", build=ComposeBuild(context=context))
        }
    )


def services(file: str) -> dict[str, Any]:
    with open(file, encoding="utf-8") as handle:
        return dict(yaml.safe_load(handle)["services"])


def snapshot(directory: Path) -> dict[str, bytes]:
    return {
        path.relative_to(directory).as_posix(): path.read_bytes()
        for path in sorted(directory.rglob("*"))
        if path.is_file()
    }


async def sample_project(config: str | ComposeConfig | None) -> ComposeProject:
    """Create and register a project the way `sample_init` does."""
    project = await ComposeProject.create(
        name=task_project_name("task_a"), config=config
    )
    project_startup(project)
    return project


async def failing_task_init(
    monkeypatch: pytest.MonkeyPatch,
    built: list[str],
    config: str | ComposeConfig | None,
) -> str:
    """Run a `task_init` whose build fails; return the file it built from."""

    async def failing_build(project: ComposeProject, *args: Any, **kwargs: Any) -> None:
        assert project.config is not None
        built.append(project.config)
        raise RuntimeError("Failed to build podman containers")

    monkeypatch.setattr(podman, "compose_build", failing_build)
    with pytest.raises(RuntimeError, match="Failed to build"):
        await PodmanSandboxEnvironment.task_init("task_b", config)
    return built[-1]


async def test_failed_setup_keeps_active_inline_sample(
    monkeypatch: pytest.MonkeyPatch, no_podman: list[str], data_dir: Path
) -> None:
    project_cleanup_startup()
    a = await sample_project(inline_config("a"))
    assert a.config is not None
    assert Path(a.config).parent == data_dir.resolve()
    # a relative build context still resolves against the working directory
    assert services(a.config) == {
        "a": {"image": "alpine", "build": {"context": os.path.join(os.getcwd(), "ctx")}}
    }

    b_file = await failing_task_init(monkeypatch, no_podman, inline_config("b"))

    assert Path(a.config).exists()
    assert list(services(a.config)) == ["a"]
    assert b_file != a.config
    assert not Path(b_file).exists()


async def test_failed_setup_keeps_active_default_sample(
    monkeypatch: pytest.MonkeyPatch, no_podman: list[str]
) -> None:
    project_cleanup_startup()
    a = await sample_project(None)
    assert a.config is not None

    b_file = await failing_task_init(monkeypatch, no_podman, None)

    assert Path(a.config).exists()
    assert list(services(a.config)) == ["default"]
    assert b_file != a.config
    assert not Path(b_file).exists()


async def test_setup_removes_its_own_file(no_podman: list[str]) -> None:
    await PodmanSandboxEnvironment.task_init("task_b", None)

    assert len(no_podman) == 1
    assert not Path(no_podman[0]).exists()


async def test_explicit_dockerfile_builds_from_its_directory(work_dir: Path) -> None:
    env = work_dir / "env"
    env.mkdir()
    (env / "Dockerfile").write_text("FROM alpine\n")

    project_cleanup_startup()
    a = await sample_project("env/Dockerfile")
    assert a.config is not None
    assert services(a.config)["default"]["build"] == {
        "context": env.as_posix(),
        "dockerfile": "Dockerfile",
    }


@pytest.mark.parametrize("case", ["explicit", "discovered", "inline"])
async def test_build_context_unchanged_across_projects(
    case: str, work_dir: Path
) -> None:
    (work_dir / "Dockerfile").write_text("FROM alpine\nCOPY . /src\n")
    (work_dir / "app.py").write_text("print('hello')\n")
    before = snapshot(work_dir)
    config: str | ComposeConfig | None = {
        "explicit": "Dockerfile",
        "discovered": None,
        "inline": inline_config("default", context="."),
    }[case]

    project_cleanup_startup()
    projects = [await sample_project(config) for _ in range(2)]

    assert snapshot(work_dir) == before
    for project in projects:
        assert project.config is not None
        assert services(project.config)["default"]["build"]["context"] == (
            work_dir.as_posix()
        )


async def test_explicit_compose_file_is_used_and_kept(
    monkeypatch: pytest.MonkeyPatch,
    no_podman: list[str],
    work_dir: Path,
    data_dir: Path,
) -> None:
    compose = work_dir / "compose.yaml"
    compose.write_text("services:\n  default:\n    image: alpine\n")

    project_cleanup_startup()
    a = await sample_project("compose.yaml")
    discovered = await sample_project(None)
    assert a.config == compose.as_posix()
    assert discovered.config == a.config
    assert auto_compose_files() == set()

    b_file = await failing_task_init(monkeypatch, no_podman, "compose.yaml")
    assert b_file == a.config
    await project_cleanup(a)
    await project_cleanup_shutdown(True)

    assert compose.exists()
    assert sorted(p.name for p in work_dir.iterdir()) == ["compose.yaml"]
    assert list(data_dir.iterdir()) == []


async def test_concurrent_inline_samples_keep_their_own_services(
    work_dir: Path, data_dir: Path
) -> None:
    project_cleanup_startup()
    projects: dict[str, ComposeProject] = {}

    async def start(service: str) -> None:
        projects[service] = await sample_project(inline_config(service))

    async with anyio.create_task_group() as tg:
        tg.start_soon(start, "a")
        tg.start_soon(start, "b")

    a, b = projects["a"], projects["b"]
    assert a.config is not None and b.config is not None
    assert a.config != b.config
    assert list(services(a.config)) == ["a"]
    assert list(services(b.config)) == ["b"]

    await project_cleanup(a)
    assert not Path(a.config).exists()
    assert list(services(b.config)) == ["b"]
    assert auto_compose_files() == {b.config}

    await project_cleanup_shutdown(True)
    assert not Path(b.config).exists()
    assert list(data_dir.iterdir()) == []
    assert list(work_dir.iterdir()) == []


@pytest.mark.parametrize("prefix", ["io.podman.compose", "com.docker.compose"])
@pytest.mark.parametrize("case", ["absolute", "traversal", "generated"])
async def test_cli_cleanup_deletes_only_generated_files(
    monkeypatch: pytest.MonkeyPatch,
    work_dir: Path,
    data_dir: Path,
    prefix: str,
    case: str,
) -> None:
    victim = work_dir / "victim" / "compose.yaml"
    victim.parent.mkdir()
    victim.write_text("services:\n  default:\n    image: alpine\n")
    generated = data_dir / "inspect-task-iabcdef.yaml"
    generated.write_text("services:\n  default:\n    image: alpine\n")
    project, config = {
        "absolute": ((work_dir / "victim" / "compose").as_posix(), victim),
        "traversal": ("../work/victim/compose", victim),
        "generated": ("inspect-task-iabcdef", generated),
    }[case]
    container = {
        "Id": "abc123",
        "Names": ["sandbox_1"],
        "Labels": {
            f"{prefix}.project": project,
            f"{prefix}.project.working_dir": config.parent.as_posix(),
            f"{prefix}.project.config_files": config.name,
        },
    }

    async def podman_ps(*args: Any, **kwargs: Any) -> ExecResult[str]:
        return ExecResult(
            success=True, returncode=0, stdout=json.dumps([container]), stderr=""
        )

    monkeypatch.setattr("inspect_podman.cleanup.subprocess", podman_ps)
    await cli_cleanup("sandbox_1")

    assert victim.exists()
    assert generated.exists() == (case != "generated")


@pytest.mark.parametrize(
    "build",
    [
        "https://github.com/docker-library/hello-world.git",
        ComposeBuild(context="git@github.com:docker-library/hello-world.git"),
        "ssh://git@example.com/repo.git",
    ],
)
async def test_inline_remote_build_context_is_refused(
    build: str | ComposeBuild, data_dir: Path
) -> None:
    config = ComposeConfig(services={"default": ComposeService(build=build)})

    with pytest.raises(ValueError, match="Remote build contexts are not supported"):
        await ComposeProject.create(name=task_project_name("task"), config=config)
    assert list(data_dir.iterdir()) == []


@pytest.mark.parametrize(
    "build", ["${BUILD_CONTEXT}", ComposeBuild(context="$BUILD_CONTEXT/app")]
)
async def test_inline_build_context_variable_is_refused(
    build: str | ComposeBuild, data_dir: Path
) -> None:
    config = ComposeConfig(services={"default": ComposeService(build=build)})

    with pytest.raises(ValueError, match="Variables in build contexts"):
        await ComposeProject.create(name=task_project_name("task"), config=config)
    assert list(data_dir.iterdir()) == []
