"""Podman sandbox environment for Inspect."""

from __future__ import annotations

import base64
import errno
import json
import os
import shlex
import tempfile
from logging import getLogger
from pathlib import Path, PurePosixPath
from typing import Literal, overload

from inspect_ai._util.error import PrerequisiteError
from inspect_ai.log._samples import sample_active
from inspect_ai.util import (
    ExecResult,
    OutputLimitExceededError,
    SandboxConnection,
    SandboxEnvironment,
    SandboxEnvironmentConfigType,
    SandboxEnvironmentLimits,
    subprocess,
)
from inspect_ai.util._sandbox.environment import HostMapping, PortMapping

from .cleanup import (
    project_cleanup,
    project_cleanup_shutdown,
    project_cleanup_startup,
    project_record_auto_compose,
    project_startup,
)
from .compose import (
    compose_build,
    compose_check_running,
    compose_cleanup_images,
    compose_pull,
    compose_services,
    compose_up,
    compose_wait_for_health,
    podman_ps,
)
from .internal import build_internal_image, is_internal_image
from .prereqs import validate_prereqs
from .util import ComposeProject, task_project_name

logger = getLogger(__name__)


class PodmanSandboxEnvironment(SandboxEnvironment):
    """Sandbox environment that executes commands in Podman containers."""

    @classmethod
    def config_files(cls) -> list[str]:
        """Return compose and build files to auto-discover."""
        from .config import COMPOSE_FILES, CONTAINERFILE, DOCKERFILE

        return COMPOSE_FILES + [DOCKERFILE, CONTAINERFILE]

    @classmethod
    def default_concurrency(cls) -> int | None:
        """Return the default maximum number of parallel sandboxes."""
        count = os.cpu_count() or 1
        return 2 * count

    @classmethod
    async def task_init(
        cls, task_name: str, config: SandboxEnvironmentConfigType | None
    ) -> None:
        """Initialize shared resources for a task."""
        await validate_prereqs()
        project_cleanup_startup()

        try:
            project = await ComposeProject.create(
                name=task_project_name(task_name), config=config
            )
            project_record_auto_compose(project)

            await compose_build(project)
            await compose_cleanup_images(project, timeout=60)

            services = await compose_services(project)

            for name, service in services.items():
                container_name = service.get("container_name")
                if container_name:
                    raise PrerequisiteError(
                        "ERROR: Podman service '{}' includes an explicitly configured "
                        "container_name ('{}'). This is not permitted, "
                        "as container names should be provisioned by compose and "
                        "explicit names break epochs.".format(name, container_name)
                    )

                image = service.get("image")
                if image and is_internal_image(image):
                    await build_internal_image(image)
                elif service.get("build") is None and service.get("x-local") is None:
                    pull_result = await compose_pull(name, project)
                    if not pull_result.success:
                        image_name = service.get("image", "(unknown)")
                        logger.error(
                            "Failed to pull podman image '%s': %s",
                            image_name,
                            pull_result.stderr,
                        )
            print("")

        except BaseException as ex:
            await project_cleanup_shutdown(True)
            raise ex

    @classmethod
    async def sample_init(
        cls,
        task_name: str,
        config: SandboxEnvironmentConfigType | None,
        metadata: dict[str, str],
    ) -> dict[str, SandboxEnvironment]:
        """Create per-sample sandbox environments."""
        env = resolve_config_environment(config, metadata)

        sample = sample_active()
        project = await ComposeProject.create(
            name=task_project_name(task_name),
            config=config,
            sample_id=sample.sample.id if sample is not None else None,
            epoch=sample.epoch if sample is not None else None,
            env=env,
        )

        project_startup(project)

        try:
            services = await compose_services(project)
            await compose_up(project, services)
            await compose_wait_for_health(project, services)

            running_services = await compose_check_running(
                list(services.keys()), project=project
            )
            if not running_services:
                raise RuntimeError("No services started for podman sandbox")

            running_containers = await podman_ps(project.name, status="running")
            container_by_service = {
                container.get("Service"): container.get("Name")
                for container in running_containers
                if container.get("Service")
            }

            default_service: str | None = None
            environments: dict[str, SandboxEnvironment] = {}
            for service, info in services.items():
                if service not in running_services:
                    continue

                container = container_by_service.get(service)
                if not container:
                    continue

                working_dir = await container_working_dir(container)
                podman_env = PodmanSandboxEnvironment(
                    service, project, container, working_dir
                )

                if info.get("x-default", False):
                    default_service = service

                environments[service] = podman_env

            if "default" not in environments and default_service is None:
                raise RuntimeError(
                    "No 'default' service found in compose file. "
                    + "Name a service 'default' or add 'x-default: true'."
                )

            default_service = default_service or "default"
            if default_service not in environments:
                raise RuntimeError(
                    "Default service "
                    f"'{default_service}' is not running for podman sandbox."
                )

            default_environment = environments[default_service]
            del environments[default_service]
            environments = {default_service: default_environment} | environments

        except BaseException as ex:
            await project_cleanup(project, True)
            raise ex

        return environments

    @classmethod
    async def sample_cleanup(
        cls,
        task_name: str,
        config: SandboxEnvironmentConfigType | None,
        environments: dict[str, SandboxEnvironment],
        interrupted: bool,
    ) -> None:
        """Cleanup environments after a sample completes."""
        if not interrupted:
            project = (
                next(iter(environments.values()))
                .as_type(PodmanSandboxEnvironment)
                ._project
            )
            await project_cleanup(project=project, quiet=True)

    @classmethod
    async def task_cleanup(
        cls, task_name: str, config: SandboxEnvironmentConfigType | None, cleanup: bool
    ) -> None:
        """Finalize task resources after all samples."""
        await project_cleanup_shutdown(cleanup)

    @classmethod
    async def cli_cleanup(cls, id: str | None) -> None:
        """Cleanup resources via Inspect CLI."""
        from .cleanup import cli_cleanup

        await cli_cleanup(id)

    def __init__(
        self, service: str, project: ComposeProject, container: str, working_dir: str
    ) -> None:
        """Create a sandbox environment bound to a container."""
        super().__init__()
        self._service = service
        self._project = project
        self._container = container
        self._working_dir = working_dir

    async def exec(
        self,
        cmd: list[str],
        input: str | bytes | None = None,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
        user: str | None = None,
        timeout: int | None = None,
        timeout_retry: bool = True,
        concurrency: bool = True,
    ) -> ExecResult[str]:
        """Execute a command in the container."""
        args = ["podman", "exec"]
        if input is not None:
            args.append("--interactive")

        final_cwd = PurePosixPath(self._working_dir if cwd is None else cwd)
        if not final_cwd.is_absolute():
            final_cwd = PurePosixPath(self._working_dir) / final_cwd

        args.extend(["--workdir", str(final_cwd)])

        if user:
            args.extend(["--user", user])

        if env:
            for key, value in env.items():
                args.extend(["--env", f"{key}={value}"])

        args.append(self._container)
        args.extend(cmd)

        exec_result = await _run_podman_command(
            args,
            input=input,
            timeout=timeout,
            timeout_retry=timeout_retry,
            concurrency=concurrency,
            output_limit=SandboxEnvironmentLimits.MAX_EXEC_OUTPUT_SIZE,
        )
        verify_exec_result_size(exec_result)

        combined = f"{exec_result.stdout}{exec_result.stderr}".lower()
        if exec_result.returncode == 126 and "permission denied" in combined:
            raise PermissionError(f"Permission denied executing command: {exec_result}")

        return exec_result

    async def write_file(self, file: str, contents: str | bytes) -> None:
        """Write a file into the container."""
        timeout = 180
        file = self.container_file(file)

        parent = Path(file).parent.as_posix()
        if parent != ".":
            result = await self.exec(["mkdir", "-p", parent])
            if not result.success:
                raise RuntimeError(f"Failed to create container directory {parent}")

        if isinstance(contents, str):
            result = await self.exec(
                [
                    "sh",
                    "-e",
                    "-c",
                    'tee -- "$1" > /dev/null',
                    "write_file_script",
                    file,
                ],
                input=contents,
                timeout=timeout,
            )
        else:
            base64_contents = base64.b64encode(contents).decode("US-ASCII")
            result = await self.exec(
                [
                    "sh",
                    "-e",
                    "-c",
                    'base64 -d | tee -- "$1" > /dev/null',
                    "write_file_script",
                    file,
                ],
                input=base64_contents,
                timeout=timeout,
            )
        if result.returncode != 0:
            if "permission denied" in result.stderr.casefold():
                ls_result = await self.exec(["ls", "-la", "."])
                error_string = (
                    "Permission was denied. Error details: "
                    f"{result.stderr}; ls -la: {ls_result.stdout}"
                )
                raise PermissionError(error_string)
            if "is a directory" in result.stderr.casefold():
                raise IsADirectoryError(
                    f"Failed to write file: {file} because it is a directory already"
                )
            raise RuntimeError(f"failed to write file: {result}")

    @overload
    async def read_file(self, file: str, text: Literal[True] = True) -> str: ...

    @overload
    async def read_file(self, file: str, text: Literal[False]) -> bytes: ...

    async def read_file(self, file: str, text: bool = True) -> str | bytes:
        """Read a file from the container."""
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
            original_file = file
            file = self.container_file(file)

            dest_file = os.path.join(temp_dir, os.path.basename(file))
            result = await _run_podman_command(
                ["podman", "cp", f"{self._container}:{file}", dest_file],
                timeout=120,
                output_limit=SandboxEnvironmentLimits.MAX_READ_FILE_SIZE,
            )
            if not result.success:
                message = result.stderr.lower()
                if "no such file" in message:
                    raise FileNotFoundError(
                        errno.ENOENT, "No such file or directory.", original_file
                    )
                if "permission denied" in message:
                    raise PermissionError(
                        errno.EACCES, "Permission denied.", original_file
                    )
                raise RuntimeError(result.stderr)

            verify_read_file_size(dest_file)

            if text:
                with open(dest_file, "r", newline="", encoding="utf-8") as handle:
                    return handle.read()
            with open(dest_file, "rb") as handle:
                return handle.read()

    async def connection(self, *, user: str | None = None) -> SandboxConnection:
        """Return shell connection info for the container."""
        if not await _container_running(self._container):
            raise ConnectionError(
                f"Service '{self._service}' is not currently running."
            )

        return SandboxConnection(
            type="podman",
            command=shlex.join(
                [
                    "podman",
                    "exec",
                    "-it",
                    *(["--user", user] if user else []),
                    self._container,
                    "sh",
                ]
            ),
            vscode_command=None,
            ports=await get_ports_info(self._container),
            container=self._container,
        )

    def default_polling_interval(self) -> float:
        """Return default polling interval for sandbox services."""
        return 0.2

    def container_file(self, file: str) -> str:
        """Resolve a path relative to the container working dir."""
        path = Path(file)
        if not path.is_absolute():
            path = Path(self._working_dir) / path
        return path.as_posix()


def resolve_config_environment(
    config: SandboxEnvironmentConfigType | None,
    metadata: dict[str, str],
) -> dict[str, str]:
    """Resolve compose environment variables from sample metadata."""
    if isinstance(config, str) and Path(config).exists():
        with open(config, "r", encoding="utf-8") as handle:
            config_text = handle.read()

        env: dict[str, str] = {}
        for key, value in metadata.items():
            env_key = f"SAMPLE_METADATA_{key.replace(' ', '_').upper()}"
            if env_key in config_text:
                env[env_key] = str(value)
        return env

    return {}


async def container_working_dir(container: str, default: str = "/") -> str:
    """Return the container working directory."""
    result = await _run_podman_command(
        ["podman", "exec", container, "sh", "-c", "pwd"], timeout=60
    )
    if result.success:
        return result.stdout.strip()

    logger.warning(
        "Failed to get working directory for podman container '%s': %s",
        container,
        result.stderr,
    )
    return default


async def _run_podman_command(
    cmd: list[str],
    *,
    input: str | bytes | None = None,
    timeout: int | None = None,
    timeout_retry: bool = True,
    concurrency: bool = True,
    output_limit: int | None = None,
) -> ExecResult[str]:
    if timeout is None:
        return await subprocess(
            cmd,
            input=input,
            timeout=timeout,
            output_limit=output_limit,
            concurrency=concurrency,
        )

    retries = 0
    retry_timeouts = [timeout, min(timeout, 60), min(timeout, 30)]
    while True:
        try:
            return await subprocess(
                cmd,
                input=input,
                timeout=retry_timeouts[min(retries, 2)],
                output_limit=output_limit,
                concurrency=concurrency,
            )
        except TimeoutError as ex:
            retries += 1
            if not timeout_retry or retries > 2:
                raise ex


async def _container_running(container: str) -> bool:
    result = await subprocess(
        ["podman", "ps", "--filter", f"name={container}", "--format", "json"],
        timeout=30,
    )
    if not result.success:
        return False

    output = result.stdout.strip()
    if not output:
        return False

    containers = json.loads(output)
    if not isinstance(containers, list):
        return False
    return bool(containers)


async def get_ports_info(container: str) -> list[PortMapping] | None:
    """Return port mappings from podman inspect."""
    try:
        result = await subprocess(
            [
                "podman",
                "inspect",
                container,
                "--format",
                "{{json .NetworkSettings.Ports}}",
            ],
            timeout=60,
        )

        if not result.success:
            raise RuntimeError(result.stderr)

        return parse_docker_inspect_ports(result.stdout)
    except TimeoutError:
        return None


def parse_docker_inspect_ports(json_str: str) -> list[PortMapping] | None:
    """Parse podman inspect port mappings."""
    json_str = json_str.strip()
    if not json_str:
        return None
    data = json.loads(json_str)
    if not isinstance(data, dict):
        return None

    port_mappings: list[PortMapping] = []
    for port_protocol, mappings in data.items():
        if mappings is None:
            continue

        if "/" not in port_protocol:
            continue
        container_port_raw, protocol = port_protocol.split("/", 1)
        try:
            container_port = int(container_port_raw)
        except ValueError:
            continue

        if not isinstance(mappings, list):
            continue

        host_mappings: list[HostMapping] = []
        for mapping in mappings:
            if not isinstance(mapping, dict):
                continue
            host_ip = mapping.get("HostIp")
            host_port_raw = mapping.get("HostPort")
            if not isinstance(host_ip, str):
                continue
            try:
                host_port = int(str(host_port_raw))
            except (TypeError, ValueError):
                continue
            host_mappings.append(HostMapping(host_ip=host_ip, host_port=host_port))

        if not host_mappings:
            continue

        port_mapping = PortMapping(
            container_port=container_port,
            protocol=protocol,
            mappings=host_mappings,
        )
        port_mappings.append(port_mapping)

    return port_mappings or None


def verify_exec_result_size(exec_result: ExecResult[str]) -> None:
    """Verify exec output size against sandbox limits."""
    limit = SandboxEnvironmentLimits.MAX_EXEC_OUTPUT_SIZE
    stdout_len = _byte_len(exec_result.stdout)
    stderr_len = _byte_len(exec_result.stderr)
    if stdout_len <= limit and stderr_len <= limit:
        return

    stdout = _truncate_to_bytes(exec_result.stdout, limit)
    stderr = _truncate_to_bytes(exec_result.stderr, limit)
    raise OutputLimitExceededError(
        limit_str=SandboxEnvironmentLimits.MAX_EXEC_OUTPUT_SIZE_STR,
        truncated_output=f"{stdout}{stderr}",
    )


def verify_read_file_size(file_path: str) -> None:
    """Verify file size against sandbox limits."""
    file_size = Path(file_path).stat().st_size
    if file_size > SandboxEnvironmentLimits.MAX_READ_FILE_SIZE:
        raise OutputLimitExceededError(
            limit_str=SandboxEnvironmentLimits.MAX_READ_FILE_SIZE_STR,
            truncated_output=None,
        )


def _byte_len(text: str) -> int:
    return len(text.encode("utf-8", errors="replace"))


def _truncate_to_bytes(text: str, max_bytes: int) -> str:
    encoded = text.encode("utf-8", errors="replace")
    if len(encoded) <= max_bytes:
        return text
    truncated = encoded[-max_bytes:]
    return truncated.decode("utf-8", errors="replace")
