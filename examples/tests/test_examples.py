"""
Test that the examples run without error.
"""

import os
import importlib
import logging
import runpy
import sys
from unittest.mock import patch

import pygfx as gfx
import imageio.v3 as iio
import numpy as np
import pytest

from examples.tests.testutils import (
    wgpu_backend,
    is_lavapipe,
    find_examples,
    ROOT,
    screenshots_dir,
    diffs_dir,
)


# run all tests unless they opt-out
examples_to_run = find_examples(negative_query="# run_example = false")

# only test output of examples that opt-in
examples_to_test = find_examples(query="# test_example = true")


class LogHandler(logging.Handler):
    def __init__(self, *args):
        super().__init__(*args)
        self.records = []

    def emit(self, record):
        if record.name in ["trimesh", "imageio"]:
            return
        self.records.append(record)


log_handler = LogHandler(logging.WARN)
logging.getLogger().addHandler(log_handler)


# Initialize the device, to avoid Rust warnings from showing in the first example
gfx.renderers.wgpu.get_shared()


@pytest.mark.parametrize("module", examples_to_run, ids=lambda x: x.stem)
def test_examples_run(module, force_offscreen):
    """Run every example marked to see if they can run without error."""
    # use runpy so the module is not actually imported (and can be gc'd)
    # but also to be able to run the code in the __main__ block

    # (relative) module name from project root
    module_name = module.relative_to(ROOT).with_suffix("").as_posix().replace("/", ".")

    # Reset logged warnings/errors
    log_handler.records = []

    runpy.run_module(module_name, run_name="__main__")

    # If any erors occured in the draw callback, they are logged
    if log_handler.records:
        raise RuntimeError("Example generated errors during draw")


@pytest.fixture
def force_offscreen():
    """Force the offscreen canvas to be selected by the auto gui module."""
    os.environ["WGPU_FORCE_OFFSCREEN"] = "true"
    try:
        yield
    finally:
        del os.environ["WGPU_FORCE_OFFSCREEN"]


@pytest.fixture
def mock_time():
    """Some examples use time to animate. Fix the return value
    for repeatable output."""
    with patch("time.time") as time_mock:
        time_mock.return_value = 1.23456
        yield


def test_that_we_are_on_lavapipe():
    print(wgpu_backend)
    if os.getenv("PYGFX_EXPECT_LAVAPIPE"):
        assert is_lavapipe


@pytest.mark.parametrize("module", examples_to_test, ids=lambda x: x.stem)
def test_examples_screenshots(
    module, pytestconfig, force_offscreen, mock_time, request
):
    """Run every example marked for testing."""

    # (relative) module name from project root
    module_name = module.relative_to(ROOT).with_suffix("").as_posix().replace("/", ".")

    # import the example module
    example = importlib.import_module(module_name)

    # ensure it is unloaded after the test
    def unload_module():
        del sys.modules[module_name]

    request.addfinalizer(unload_module)

    # render a frame
    img = np.asarray(example.renderer.target.draw())

    # check if _something_ was rendered
    assert img is not None and img.size > 0

    # we skip the rest of the test if you are not using lavapipe
    # images come out subtly differently when using different wgpu adapters
    # so for now we only compare screenshots generated with the same adapter (lavapipe)
    # a benefit of using pytest.skip is that you are still running
    # the first part of the test everywhere else; ensuring that examples
    # can at least import, run and render something
    if not is_lavapipe:
        pytest.skip("screenshot comparisons are only done when using lavapipe")

    # regenerate screenshot if requested
    screenshot_path = screenshots_dir / f"{module.stem}.png"
    if pytestconfig.getoption("regenerate_screenshots"):
        iio.imwrite(screenshot_path, img)

    # if a reference screenshot exists, assert it is equal
    assert (
        screenshot_path.exists()
    ), "found # test_example = true but no reference screenshot available"
    stored_img = iio.imread(screenshot_path)
    # assert similarity
    is_similar = np.allclose(img, stored_img, atol=1)
    update_diffs(module.stem, is_similar, img, stored_img)
    assert is_similar, (
        f"rendered image for example {module.stem} changed, see "
        f"the {diffs_dir.relative_to(ROOT).as_posix()} folder"
        " for visual diffs (you can download this folder from"
        " CI build artifacts as well)"
    )


def update_diffs(module, is_similar, img, stored_img):
    diffs_dir.mkdir(exist_ok=True)

    diffs_rgba = None

    def get_diffs_rgba(slicer):
        # lazily get and cache the diff computation
        nonlocal diffs_rgba
        if diffs_rgba is None:
            # cast to float32 to avoid overflow
            # compute absolute per-pixel difference
            diffs_rgba = np.abs(stored_img.astype("f4") - img)
            # magnify small values, making it easier to spot small errors
            diffs_rgba = ((diffs_rgba / 255) ** 0.25) * 255
            # cast back to uint8
            diffs_rgba = diffs_rgba.astype("u1")
        return diffs_rgba[..., slicer]

    # split into an rgb and an alpha diff
    diffs = {
        diffs_dir / f"{module}-rgb.png": slice(0, 3),
        diffs_dir / f"{module}-alpha.png": 3,
    }

    for path, slicer in diffs.items():
        if not is_similar:
            diff = get_diffs_rgba(slicer)
            iio.imwrite(path, diff)
        elif path.exists():
            path.unlink()


if __name__ == "__main__":
    # Enable tweaking in an IDE by running in an interactive session.
    os.environ["WGPU_FORCE_OFFSCREEN"] = "true"
    pytest.getoption = lambda x: False
    is_lavapipe = True  # noqa: F811
    test_examples_screenshots("validate_volume", pytest, None, None)
