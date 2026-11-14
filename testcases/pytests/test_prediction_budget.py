# Copyright 2004-2026 Tom Rothamel <pytom@bishoujo.us>
#
# Permission is hereby granted, free of charge, to any person
# obtaining a copy of this software and associated documentation files
# (the "Software"), to deal in the Software without restriction,
# including without limitation the rights to use, copy, modify, merge,
# publish, distribute, sublicense, and/or sell copies of the Software,
# and to permit persons to whom the Software is furnished to do so,
# subject to the following conditions:
#
# The above copyright notice and this permission notice shall be
# included in all copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND,
# EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF
# MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND
# NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE
# LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION
# OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION
# WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.

import pytest

from renpy.display.core import Interface


@pytest.mark.parametrize("expensive, steps", [(False, 1), (True, 3)])
def test_forced_prediction_respects_animation_budget(mocker, expensive, steps):
    """Forced prediction waits for completion while still yielding to an animated screen; idle prediction can drain."""

    interface = mocker.Mock(spec=Interface)
    interface.texture_task = mocker.Mock()
    interface.prediction_task = mocker.Mock()
    interface.texture_task.done.return_value = False
    interface.prediction_task.done.return_value = False
    interface.force_prediction = True
    interface.event_peek.return_value = False
    run = mocker.patch("renpy.asynctask.run_for_ns", side_effect=[True, True, False])

    Interface.run_tasks(interface, expensive)

    assert run.call_count == steps
