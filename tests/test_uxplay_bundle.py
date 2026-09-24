"""The AirPlay receiver the app carries must not reach back to the build machine.

UxPlay is GStreamer, and GStreamer is not one binary: it loads plugins at run
time from a path it is told about, and spawns a scanner to index them. So a
staged receiver can pass `otool -L` and still fail the moment a pipeline is
built, because the element it wants lives in a plugin that was never copied.
Both halves are checked here: what the Mach-O files load, and what the plugin
registry can actually resolve.

Only the elements the headless pipeline uses are staged -- `-vs fakesink` and
`-as 0` mean no audio and no window -- which is 31 MB rather than the 162 MB of
plugins Homebrew installs.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

from circuit_mcp import paths

SCRIPT = Path(__file__).resolve().parents[1] / "macos" / "stage_uxplay.sh"
INSIDE = ("@rpath/", "@loader_path/", "@executable_path/", "/usr/lib/", "/System/")
# appsrc ! queue ! h264parse ! decodebin ! videoconvert ! videoscale ! fakesink
PIPELINE = ("appsrc", "queue", "h264parse", "decodebin", "videoconvert", "videoscale",
            "fakesink", "typefind", "vtdec_hw")

pytestmark = pytest.mark.skipif(
    sys.platform != "darwin" or shutil.which("otool") is None
    or not (paths.REPO_ROOT / ".local/runtime/uxplay/bin/uxplay").is_file(),
    reason="needs a Mac with UxPlay built by scripts/setup_ipad_capture.sh",
)


@pytest.fixture(scope="module")
def staged(tmp_path_factory) -> Path:
    destination = tmp_path_factory.mktemp("uxplay-bundle") / "uxplay"
    run = subprocess.run([str(SCRIPT), str(destination)], capture_output=True, text=True,
                         timeout=600)
    assert run.returncode == 0, run.stdout + run.stderr
    return destination


def bundled_environment(staged: Path) -> dict[str, str]:
    """What the app has to set for the staged GStreamer to be the one that runs."""
    return {
        "PATH": "/usr/bin:/bin",
        "GST_PLUGIN_SYSTEM_PATH": str(staged / "plugins"),
        "GST_PLUGIN_PATH": str(staged / "plugins"),
        "GST_PLUGIN_SCANNER": str(staged / "libexec" / "gst-plugin-scanner"),
        "GST_REGISTRY": str(staged / "registry.bin"),
    }


def loads(binary: Path) -> list[str]:
    out = subprocess.run(["otool", "-L", str(binary)], capture_output=True, text=True, check=True)
    return [line.split(" (")[0].strip() for line in out.stdout.splitlines()[1:]]


def test_the_staged_receiver_loads_its_libraries(staged):
    """-v proves the Mach-O side only: it never reaches the plugin check below."""
    run = subprocess.run([str(staged / "bin" / "uxplay"), "-v"], capture_output=True,
                         text=True, timeout=60, env=bundled_environment(staged))
    assert "UxPlay" in run.stdout + run.stderr, run.stdout + run.stderr


def test_the_staged_receiver_actually_starts_serving(staged):
    """The check that matters, and the one the other three cannot make.

    UxPlay asks the registry for app, libav, playback, autodetect and
    videoparsersbad before it serves anything -- a fixed list in
    audio_renderer.c, unrelated to the flags it was given. A bundle can pass
    every otool check and every `gst-inspect --exists` for the pipeline and
    still refuse here, which is what happened: libav and autodetect were not
    staged, and the first real start said so.
    """
    receiver = subprocess.Popen(
        [str(staged / "bin" / "uxplay"), "-n", "Bundle Test", "-nh", "-vs", "fakesink", "-as", "0"],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        env=bundled_environment(staged))
    try:
        time.sleep(4)
        alive = receiver.poll() is None
    finally:
        receiver.terminate()
        try:
            output = receiver.communicate(timeout=15)[0]
        except subprocess.TimeoutExpired:
            receiver.kill()
            output = receiver.communicate(timeout=15)[0]
    assert "not found" not in output, output[-800:]
    assert alive, f"the receiver exited instead of serving:\n{output[-800:]}"


def test_every_plugin_uxplay_demands_at_startup_is_staged(staged):
    """Named rather than inferred: this list is UxPlay's, not the pipeline's."""
    present = {path.name for path in (staged / "plugins").glob("*.dylib")}
    for required in ("app", "libav", "playback", "autodetect", "videoparsersbad"):
        assert f"libgst{required}.dylib" in present, f"UxPlay refuses to start without {required}"


def test_nothing_staged_loads_a_library_from_outside_the_bundle(staged):
    binaries = [staged / "bin" / "uxplay",
                *sorted((staged / "lib").glob("*.dylib")),
                *sorted((staged / "plugins").glob("*.dylib")),
                staged / "libexec" / "gst-plugin-scanner"]
    assert len(binaries) > 20, "the receiver links a great deal more than itself"
    outside = {f"{binary.name}: {load}"
               for binary in binaries if binary.exists()
               for load in loads(binary)
               if not load.startswith(INSIDE)}
    assert outside == set()


def test_every_element_the_pipeline_needs_resolves_from_the_bundled_plugins(staged):
    """otool cannot see this: a missing plugin is a run-time failure, not a link error."""
    inspect = staged / "bin" / "gst-inspect-1.0"
    environment = bundled_environment(staged)
    missing = [element for element in PIPELINE
               if subprocess.run([str(inspect), "--exists", element], env=environment,
                                 timeout=60).returncode != 0]
    assert missing == [], f"the staged plugins cannot provide {missing}"


def test_the_registry_is_never_written_inside_the_bundle(staged):
    """A .app is read-only once installed, and GStreamer writes its registry on
    first run. Pointed inside, every launch rescans and warns; pointed nowhere
    writable, it rescans forever."""
    assert "GST_REGISTRY" in bundled_environment(staged)
    assert not (staged / "registry.bin").is_relative_to(staged / "bin")


def test_staging_refuses_rather_than_leaving_an_empty_folder(tmp_path):
    run = subprocess.run([str(SCRIPT), str(tmp_path / "out")], capture_output=True, text=True,
                         timeout=60, env={"PATH": "/usr/bin:/bin", "UXPLAY": "/nonexistent/uxplay"})
    assert run.returncode != 0
    assert "uxplay" in run.stderr.lower()
