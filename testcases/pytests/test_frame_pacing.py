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

from renpy.display.core import _redraw_deadline


@pytest.mark.parametrize(
    ("callback", "limiter", "requested", "expected"),
    [
        (0.100, 0.010, True, 0.010),
        (None, 0.010, True, 0.010),
        (0.100, 0.010, False, 0.100),
        (0.005, 0.010, False, 0.010),
        (None, 0.010, False, None),
        (0.100, None, True, 0.100),
    ],
)
def test_redraw_deadline_respects_requested_frames(callback, limiter, requested, expected):
    """A future callback must not delay a frame that is already requested."""

    assert _redraw_deadline(callback, limiter, requested) == expected
