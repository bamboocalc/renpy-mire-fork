"""Tests for renpy.display.motion."""

import pytest

import renpy
from renpy.display.layout import Null
from renpy.display.motion import RotoZoom


@pytest.mark.parametrize("st, redraws", [(0.5, True), (2.0, False)])
def test_rotozoom_stops_redrawing_when_finished(mocker, st, redraws):
    """RotoZoom compared its times after clamping them to 1.0, so it redrew every frame forever."""

    mocker.patch.object(renpy.display.render, "render")
    redraw = mocker.patch.object(renpy.display.render, "redraw")
    rotozoom = RotoZoom(0, 360, 1.0, 1.0, 2.0, 1.0, Null())

    # The first render creates the inner transform, which orders a redraw of its own.
    rotozoom.render(100, 100, 0.0, 0.0)
    redraw.reset_mock()
    rotozoom.render(100, 100, st, st)

    assert redraw.called == redraws


@pytest.mark.parametrize("st, angle, redraws", [(0.25, 90, True), (0.5, 180, True), (0.75, 90, True), (2.0, 0, False)])
def test_rotozoom_bounce_returns_and_stops(mocker, st, angle, redraws):
    """A one-second bounce turns around at 0.5 seconds, ends where it began, and never rotates past its start."""

    mocker.patch.object(renpy.display.render, "render")
    redraw = mocker.patch.object(renpy.display.render, "redraw")
    rotozoom = RotoZoom(0, 180, 1.0, 1.0, 1.0, 0, Null(), rot_bounce=True)
    rotozoom.render(100, 100, 0.0, 0.0)
    redraw.reset_mock()
    rotozoom.render(100, 100, st, st)

    assert rotozoom.transform.rotate == pytest.approx(angle)
    assert redraw.called == redraws
