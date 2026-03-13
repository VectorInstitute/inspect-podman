"""Build and detect internal Inspect images using Podman."""

from __future__ import annotations

from inspect_ai._util.constants import PKG_PATH
from inspect_ai._util.error import PrerequisiteError
from inspect_ai.util import subprocess

INSPECT_WEB_BROWSER_IMAGE_DEPRECATED = "inspect_web_browser"
INSPECT_COMPUTER_IMAGE = "inspect-computer-tool"

INTERNAL_IMAGES = {
    INSPECT_WEB_BROWSER_IMAGE_DEPRECATED: PKG_PATH
    / "tool"
    / "_tools"
    / "_web_browser"
    / "_resources",
    INSPECT_COMPUTER_IMAGE: PKG_PATH / "tool" / "beta" / "_computer" / "_resources",
}


async def build_internal_image(image: str) -> None:
    """Build an Inspect internal image with Podman."""
    args = ["podman", "build", "--tag", image]
    result = await subprocess(
        args + [INTERNAL_IMAGES[image].as_posix()],
        capture_output=False,
    )
    if not result.success:
        raise PrerequisiteError(f"Unexpected error building Podman image '{image}'")


def is_internal_image(image: str) -> bool:
    """Return True if the image is an Inspect internal image."""
    return any(image == internal for internal in INTERNAL_IMAGES.keys())
