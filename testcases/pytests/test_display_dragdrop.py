from types import SimpleNamespace

import pytest

import renpy
from renpy.display import dragdrop
from renpy.display.core import IgnoreEvent
from renpy.display.dragdrop import Drag
from renpy.display.layout import Null


@pytest.fixture(autouse=True)
def game_log(mocker):
    """Replaces the rollback log, so drags can record mutations without a running game."""

    mocker.patch.object(renpy.game, "log", SimpleNamespace(mutated={}))


def make_drag(mocker, **kwargs):
    """Creates a focused Drag at the origin whose child ignores events, with a mocked child event method."""

    drag = Drag(Null(), **kwargs)
    drag.last_x = drag.last_y = 0
    drag.is_focused = mocker.Mock(return_value=True)
    drag.child.event = mocker.Mock(return_value=None)
    return drag


def test_ungrabbed_child_gets_event_once(mocker):
    """
    A non-draggable Drag that isn't grabbed passes an event to its child exactly once, with the original
    coordinates, and returns the child's result.

    Guards against the event being delivered to the child twice when no drag is in progress.
    """

    drag = make_drag(mocker, draggable=False)
    event = renpy.pygame.event.Event(renpy.pygame.KEYDOWN)
    mocker.patch.object(renpy.display.focus, "get_grab", return_value=None)
    mocker.patch.object(dragdrop, "map_event", return_value=False)

    assert drag.event(event, 3, 4, 0) is None
    drag.child.event.assert_called_once_with(event, 3, 4, 0)


@pytest.mark.parametrize("result", [None, "dragging"])
def test_joined_move_updates_each_position_before_callback(mocker, result):
    """
    When a grabbed drag moves together with joined drags, every joined drag's position is updated
    before the dragging callback runs.

    The callback records the positions it sees, so it must see both drags already at their new
    locations: the mouse position for the first, and that plus the (20, 30) join offset for the second.
    The drag group's positions and each drag's target are updated to match. Parametrized over the
    callback returning None, which makes the event be ignored, and returning a value, which becomes the
    result of event().
    """

    first = make_drag(mocker, drag_raise=False)
    second = make_drag(mocker)
    group = mocker.Mock(positions={})
    group.get_best_drop = mocker.Mock(return_value=None)

    for index, drag in enumerate((first, second)):
        drag.drag_name = str(index)
        drag.drag_group = group
        drag.parent_width = drag.parent_height = 200
        drag.w = drag.h = 10
        drag.old_position = (index * 20, index * 30, 0, 0, 0, 0)

    first.drag_joined = lambda drag: [(first, 0, 0), (second, 20, 30)]
    first.grab_x = first.grab_y = 0
    callback_positions = []

    def dragging(joined):
        callback_positions.append(tuple((drag.x, drag.y) for drag in joined))
        return result

    first.dragging = mocker.Mock(side_effect=dragging)
    event = renpy.pygame.event.Event(renpy.pygame.MOUSEMOTION)
    mocker.patch.object(renpy.display.focus, "get_grab", return_value=first)
    mocker.patch.object(dragdrop, "map_event", return_value=False)
    mocker.patch.object(dragdrop, "redraw")
    mocker.patch.object(first, "update_style_prefix")

    if result is None:
        with pytest.raises(IgnoreEvent):
            first.event(event, 5, 7, 0)
    else:
        assert first.event(event, 5, 7, 0) == result

    first.dragging.assert_called_once_with([first, second])
    assert callback_positions == [((5, 7), (25, 37))]
    assert group.positions["0"] == (5, 7, first.old_position)
    assert group.positions["1"] == (25, 37, second.old_position)
    for drag in (first, second):
        assert (drag.target_x, drag.target_y) == (drag.x, drag.y)


def test_drag_that_ends_the_interaction_is_no_longer_moving(mocker):
    """A dragged callback that returned a value left drag_moved set, so later hovers showed drop targets."""

    drag = make_drag(mocker, dragged=lambda drags, drop: "done")
    drag.parent_width = drag.parent_height = 200
    drag.w = drag.h = 10
    drag.grab_x = drag.grab_y = drag.start_x = drag.start_y = 0
    drag.drag_moved = True

    mocker.patch.object(renpy.display.focus, "get_grab", return_value=drag)
    mocker.patch.object(renpy.display.focus, "set_grab")
    mocker.patch.object(dragdrop, "map_event", side_effect=lambda ev, name: name == "drag_deactivate")
    mocker.patch.object(dragdrop, "redraw")
    mocker.patch.object(drag, "update_style_prefix")
    mocker.patch.object(drag, "set_style_prefix")

    event = renpy.pygame.event.Event(renpy.pygame.MOUSEBUTTONUP, button=1, pos=(5, 7))
    assert drag.event(event, 5, 7, 0) == "done"
    assert not drag.drag_moved


def test_snap_without_delay_ends_a_snap_in_progress(mocker):
    """A snap with no delay kept animating over what was left of the previous snap's time."""

    mocker.patch.object(dragdrop, "redraw")
    drag = Drag(Null())
    drag.x = drag.y = 0
    drag.at = 1.0

    drag.snap(100, 100, delay=2.0)
    drag.target_at = 3.0

    drag.snap(50, 50)

    assert drag.target_at <= drag.at
