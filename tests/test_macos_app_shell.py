"""The app shell: an icon of its own, and a build Spotlight will not list twice."""
from __future__ import annotations

import plistlib

from circuit_mcp import paths

STAGED = "dist/Circuit MCP.noindex/Circuit MCP.app"


def test_the_app_names_an_icon_that_is_in_the_repo():
    plist = plistlib.loads((paths.REPO_ROOT / "macos" / "Resources" / "Info.plist").read_bytes())
    assert plist["CFBundleIconFile"] == "AppIcon"
    icon = paths.REPO_ROOT / "macos" / "Resources" / "AppIcon.icns"
    assert icon.is_file() and icon.stat().st_size > 1000
    script = (paths.REPO_ROOT / "macos" / "build_app.sh").read_text()
    assert "AppIcon.icns" in script


def test_the_built_app_is_staged_where_spotlight_skips_it():
    """A folder whose name ends in .noindex is not indexed, so the checkout
    copy does not sit next to the installed app in Spotlight."""
    build = (paths.REPO_ROOT / "macos" / "build_app.sh").read_text()
    assert '.noindex/$APP_NAME.app' in build
    assert 'APP="$ROOT/dist/$APP_NAME.app"' not in build
    for relative in (
        "macos/smoke_test.sh",
        "macos/check_python_runtime.sh",
        "README.md",
        "docs/reference.md",
    ):
        text = (paths.REPO_ROOT / relative).read_text()
        assert STAGED in text, relative
        leftover = text.replace(STAGED, "")
        assert "dist/Circuit MCP.app" not in leftover, relative
