"""Tests for renpy.display.imagelike."""

import pytest

import renpy
from renpy.display.displayable import Displayable
from renpy.display.imagelike import Frame
from renpy.display.render import Render


class Pixel(Displayable):
    """A displayable that is always 1x1."""

    def render(self, width, height, st, at):
        return Render(1, 1)


@pytest.fixture(autouse=True)
def draw(mocker):
    """Replaces the display's draw object, so rendering doesn't touch a real graphics device."""

    rv = mocker.patch.object(renpy.display, "draw", mocker.Mock(info={"renderer": "gl2"}))
    rv.draw_to_virt.transform.return_value = (1, 1)


@pytest.fixture(autouse=True)
def default_style(mocker):
    """Defines the default style, which renpy/common/00style.rpy normally creates."""

    mocker.patch.dict(renpy.style.styles, {("default",): renpy.style.Style(None, {"xminimum": 0, "yminimum": 0})})
    mocker.patch.object(renpy.display.displayable, "default_style", renpy.style.Style("default"))


def test_frame_stretches_a_one_pixel_child():
    """A child narrower than its borders gave negative border sizes, and the frame drew nothing."""

    rv = renpy.display.render.render(Frame(Pixel(), 10, 10), 100, 50, 0, 0)

    assert rv.get_size() == (100, 50)
    assert len(rv.children) == 1
