"""Tests for renpy.gl2.live2dmotion: evaluating Cubism motion curves."""

import io
import json

import pytest

import renpy
from renpy.gl2.live2dmotion import Motion


def load_motion(mocker, duration, segments, old_beziers=False):
    """Loads a motion with one curve, from motion JSON built in memory."""

    data = {"Meta": {"Duration": duration}, "Curves": [{"Target": "Parameter", "Id": "Angle", "Segments": segments}]}
    mocker.patch.object(renpy.loader, "load", return_value=io.BytesIO(json.dumps(data).encode("utf-8")))

    return Motion("motion.json", 0.0, 0.0, old_beziers=old_beziers)


@pytest.mark.parametrize(
    "segments, old_beziers",
    [
        ([0, 0, 0, 0, 10, 0, 1, 20], False),
        ([0, 0, 1, 0, 0, 0, 0, 0, 10, 0, 1, 20], True),
    ],
)
def test_zero_length_segment_jumps_to_its_end(mocker, segments, old_beziers):
    """A linear or old-style Bezier segment with no length divided 0 by 0 when the motion reached it."""

    motion = load_motion(mocker, 1.0, segments, old_beziers)

    assert motion.get(0.0, None, False, False) == {("Parameter", "Angle"): (1.0, 10)}
    assert motion.get(0.5, None, False, False) == {("Parameter", "Angle"): (1.0, 15.0)}


def test_motion_without_duration_holds_its_values(mocker):
    """A motion with a Duration of 0 took its time modulo 0."""

    motion = load_motion(mocker, 0.0, [0, 5, 0, 0, 10])

    assert motion.get(2.0, None, False, False) == {("Parameter", "Angle"): (1.0, 10)}
    assert motion.wait(2.0, None, False, False) == 0
