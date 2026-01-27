"""Podman variant of Inspect's sandbox tutorial file listing example.

Reference: https://inspect.aisi.org.uk/sandboxing.html#example-file-listing
"""

from inspect_ai import Task, task
from inspect_ai.dataset import Sample
from inspect_ai.scorer import includes
from inspect_ai.solver import generate, use_tools
from inspect_ai.tool import ToolError, tool
from inspect_ai.util import sandbox


@tool
def list_files():
    """Expose a sandboxed file listing tool."""

    async def execute(dir: str):
        """List files in a directory within the sandbox.

        Args:
            dir: Directory to list.
        """
        result = await sandbox().exec(["ls", dir])
        if result.success:
            return result.stdout
        raise ToolError(result.stderr)

    return execute


dataset = [
    Sample(
        input='Is there a file named "bar.txt" in the current directory?',
        target="Yes",
        files={"bar.txt": "hello"},
    )
]


@task
def file_listing():
    """Inspect tutorial file listing task using Podman."""
    return Task(
        dataset=dataset,
        solver=[use_tools([list_files()]), generate()],
        # Same as the tutorial example, but use Podman instead of Docker.
        sandbox="podman",
        scorer=includes(),
    )
