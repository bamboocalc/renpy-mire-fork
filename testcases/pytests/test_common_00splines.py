"""Tests for _SplineInterpolator in renpy/common/00splines.rpy: clamping and size changes."""

import pathlib
import textwrap

import pytest

from renpy.display.position import absolute

SOURCE = pathlib.Path(__file__).parents[2] / "renpy" / "common" / "00splines.rpy"


@pytest.fixture
def interpolator():
    """The interpolator class, run from the body of the file's init python block."""

    lines = SOURCE.read_text(encoding="utf-8-sig").splitlines()
    start = next(i for i, l in enumerate(lines) if l.startswith("init -1500 python:")) + 1
    namespace = {"absolute": absolute}
    exec(textwrap.dedent("\n".join(lines[start:])), namespace)
    return namespace["_SplineInterpolator"]


SIZES = (100, 100, 0, 0)


def test_before_the_first_point_stays_on_it(interpolator):
    """Before the first point's time, the clamped result was replaced by interpolating from the last point."""

    spline = interpolator([((10, 10), 0.5), ((90, 90), 1.0)])
    assert spline(0.25, SIZES)[:2] == (10, 10)


def test_last_points_at_the_same_time_end_on_the_last(interpolator):
    """Two points at the end time divided by zero once t reached that time."""

    spline = interpolator([((0, 0), 0.0), ((50, 50), 1.0), ((90, 90), 1.0)])
    assert spline(1.0, SIZES)[:2] == (90, 90)


def test_size_changes_convert_from_the_original_points(interpolator):
    """Points were converted in place, so a second size treated the first size's pixels as fractions."""

    spline = interpolator([((0.0, 0.0),), ((0.5, 0.5),)])
    assert spline(1.0, (100, 100, 0, 0))[:2] == (50, 50)
    assert spline(1.0, (200, 200, 0, 0))[:2] == (100, 100)


def test_points_from_older_saves_are_used_as_converted(interpolator):
    """Saves made before abs_points hold points that were already converted in place."""

    spline = interpolator([((0.0, 0.0),), ((0.5, 0.5),)])
    spline.points = [[0.0, (0.0, 0.0)], [1.0, (50.0, 50.0)]]
    spline.initialized = SIZES

    assert spline(1.0, SIZES)[:2] == (50, 50)
