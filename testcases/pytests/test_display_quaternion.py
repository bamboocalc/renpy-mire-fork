"""Tests for interpolating Euler orientations through quaternions."""

import math

import pytest

from renpy.display.quaternion import euler_slerp


def rotation(x, y, z):
    """The z-y-x rotation matrix of Euler angles in degrees, the convention euler_slerp converts through."""

    x, y, z = (math.radians(i) for i in (x, y, z))
    cx, sx, cy, sy, cz, sz = math.cos(x), math.sin(x), math.cos(y), math.sin(y), math.cos(z), math.sin(z)

    return [
        [cz * cy, cz * sy * sx - sz * cx, cz * sy * cx + sz * sx],
        [sz * cy, sz * sy * sx + cz * cx, sz * sy * cx - cz * sx],
        [-sy, cy * sx, cy * cx],
    ]


def assert_same_rotation(actual, expected):
    for actual_row, expected_row in zip(rotation(*actual), rotation(*expected)):
        assert actual_row == pytest.approx(expected_row, abs=1e-6)


@pytest.mark.parametrize(("old", "new"), [((0, 0, 350), (0, 0, 10)), ((0, 0, 10), (0, 0, 350))], ids=["up", "down"])
@pytest.mark.parametrize(("complete", "offset"), [(0.25, -5), (0.5, 0), (0.75, 5)])
def test_slerp_takes_the_short_way_across_zero(old, new, complete, offset):
    """Going from 350 to 10 degrees passed through 180, the long way round, while going from 10 to 350 did not."""

    if old[2] > new[2]:
        expected = (0, 0, offset)
    else:
        expected = (0, 0, -offset)

    assert_same_rotation(euler_slerp(complete, old, new), expected)


@pytest.mark.parametrize("target", [(30, 90, 50), (30, -90, 50), (0, 90, 40)])
def test_slerp_reaches_a_gimbal_locked_orientation(target):
    """At a pitch of 90 degrees the remaining yaw had the wrong sign, so the end orientation was mirrored."""

    assert_same_rotation(euler_slerp(1.0, (0, 0, 0), target), target)
