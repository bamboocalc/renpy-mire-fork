"""Tests for renpy.display.viewport.Viewport event handling."""

import renpy
from renpy.display.layout import Null
from renpy.display.viewport import Viewport


def test_drag_ends_when_the_viewport_stops_being_draggable(mocker):
    """A viewport whose range fell to 0 mid-drag dropped its drag position but kept the grab, then unpacked None."""

    viewport = Viewport(Null(), draggable=True)
    viewport.width = viewport.height = 100
    viewport.xadjustment.range = viewport.yadjustment.range = 0

    grab = [viewport]
    mocker.patch.object(renpy.display.focus, "get_grab", side_effect=lambda: grab[0])
    mocker.patch.object(renpy.display.focus, "set_grab", side_effect=lambda g: grab.__setitem__(0, g))
    mocker.patch.object(renpy.display.behavior, "map_event", return_value=False)
    viewport.drag_position = (10, 10)

    ev = renpy.pygame.event.Event(renpy.pygame.MOUSEMOTION, pos=(20, 20), rel=(10, 10), buttons=(1, 0, 0))
    viewport.event(ev, 20, 20, 0.1)

    assert grab[0] is None
