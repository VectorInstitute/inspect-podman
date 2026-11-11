from pathlib import Path
from typing import Any

import anyio
import pytest
import yaml
from inspect_ai.util import ComposeBuild, ComposeConfig, ComposeService, ExecResult

from inspect_podman import podman
from inspect_podman.cleanup import (
    auto_compose_files,
    project_cleanup,
    project_cleanup_shutdown,
    project_cleanup_startup,
    project_startup,
)
from inspect_podman.podman import PodmanSandboxEnvironment
from inspect_podman.util import ComposeProject, task_project_name

pytestmark = pytest.mark.anyio


@pytest.fixture(params=["asyncio", "trio"])
def anyio_backend(request: pytest.FixtureRequest) -> str:
    return str(request.param)


@pytest.fixture(autouse=True)
def no_podman(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> list[str]:
    """Run in an empty directory with every Podman call stubbed out.

    Returns the compose files `compose_build` was called with.
    """
    monkeypatch.chdir(tmp_path)
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


def inline_config(service: str) -> ComposeConfig:
    return ComposeConfig(
        services={
            service: ComposeService(image="alpine", build=ComposeBuild(context="ctx"))
        }
    )


def services(file: str) -> dict[str, Any]:
    with open(file, encoding="utf-8") as handle:
        return dict(yaml.safe_load(handle)["services"])


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
    monkeypatch: pytest.MonkeyPatch, no_podman: list[str], tmp_path: Path
) -> None:
    project_cleanup_startup()
    a = await sample_project(inline_config("a"))
    assert a.config is not None
    # relative paths in the generated file resolve against the working directory
    assert Path(a.config).parent == tmp_path.resolve()
    assert services(a.config) == {"a": {"image": "alpine", "build": {"context": "ctx"}}}

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


async def test_explicit_dockerfile_generates_file_beside_it(tmp_path: Path) -> None:
    env = tmp_path / "env"
    env.mkdir()
    (env / "Dockerfile").write_text("FROM alpine\n")

    project_cleanup_startup()
    a = await sample_project("env/Dockerfile")
    assert a.config is not None
    assert Path(a.config).parent == env.resolve()
    assert services(a.config)["default"]["build"] == {
        "context": ".",
        "dockerfile": "Dockerfile",
    }


async def test_explicit_compose_file_is_used_and_kept(
    monkeypatch: pytest.MonkeyPatch, no_podman: list[str], tmp_path: Path
) -> None:
    compose = tmp_path / "compose.yaml"
    compose.write_text("services:\n  default:\n    image: alpine\n")

    project_cleanup_startup()
    a = await sample_project("compose.yaml")
    discovered = await sample_project(None)
    assert a.config == compose.resolve().as_posix()
    assert discovered.config == a.config
    assert auto_compose_files() == set()

    b_file = await failing_task_init(monkeypatch, no_podman, "compose.yaml")
    assert b_file == a.config
    await project_cleanup(a)
    await project_cleanup_shutdown(True)

    assert compose.exists()
    assert sorted(p.name for p in tmp_path.iterdir()) == ["compose.yaml"]


async def test_concurrent_inline_samples_keep_their_own_services(
    tmp_path: Path,
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
    assert list(tmp_path.iterdir()) == []
