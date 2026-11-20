"""Tests for the simulated mouse in renpy.test.testmouse and the click positions in renpy.test.testfocus."""

import copy

import pytest

import renpy
from renpy.test import testfocus, testmouse


@pytest.fixture
def mouse(mocker):
    mocker.patch.object(testmouse, "post")
    mocker.patch.object(testmouse, "mouse_buttons", [0, 0, 0])
    mocker.patch.object(testmouse, "mouse_pos", (0, 0))
    return testmouse


def test_right_button_is_tracked_and_reset(mouse):
    """The right button was never recorded as held, and reset left held buttons for the next test."""

    mouse.press_mouse(3)
    assert mouse.mouse_buttons == [0, 0, 1]

    mouse.press_mouse(1)
    mouse.reset()
    assert mouse.mouse_buttons == [0, 0, 0]


def test_buttons_without_a_slot_are_not_tracked(mouse):
    """Button 0 wrote the last slot, through index -1."""

    mouse.press_mouse(0)
    mouse.press_mouse(4)

    assert mouse.mouse_buttons == [0, 0, 0]


class FakeFocus:
    widget = None
    arg = None

    def __init__(self, x, y, w, h):
        self.x, self.y, self.w, self.h = x, y, w, h

    def copy(self):
        return copy.copy(self)


def test_focus_narrower_than_a_pixel_is_searched(mocker):
    """A focus whose width rounded to 0 picked from an empty range, which raised ValueError."""

    focus = FakeFocus(10, 20, 0.4, 0.4)
    focus_at_point = mocker.patch.object(renpy.display.render, "focus_at_point", return_value=focus)

    assert testfocus.find_position(focus, (None, None)) == (10, 20)
    focus_at_point.assert_called_with(10, 20)
