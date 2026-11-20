"""Tests for renpy.display.position."""

import pytest

from renpy.display.position import absolute, position


@pytest.mark.parametrize("value, number", [(position(10, 0), 10), (position(0, 0.5), 0.5), (position(1.5, 0), absolute(1.5))])
def test_a_position_equal_to_a_number_hashes_as_it(value, number):
    """position(10, 0) == 10, but the two hashed differently, so sets and dicts treated them as distinct."""

    assert value == number
    assert hash(value) == hash(number)
    assert len({value, number}) == 1
