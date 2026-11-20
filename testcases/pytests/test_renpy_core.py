"""Tests for the native image helpers in _renpy and the fribidi wrapper."""

import _renpy
import pytest

import renpy
from renpy.text import bidi


def surface(width, height):
    return renpy.pygame.Surface((width, height), renpy.pygame.SRCALPHA, 32)


def pixels(surf):
    return [tuple(surf.get_at((x, y))) for y in range(surf.get_height()) for x in range(surf.get_width())]


@pytest.mark.parametrize("vertical", [0, 1], ids=["horizontal", "vertical"])
def test_linblur_wider_than_the_surface_uses_the_widest_kernel_that_fits(vertical):
    """A kernel wider than a line read and wrote past the line; it now uses the widest radius that fits."""

    src = surface(4, 4)
    for x in range(4):
        for y in range(4):
            src.set_at((x, y), (x * 60, y * 60, 30, 255))

    expected = surface(4, 4)
    _renpy.linblur(src, expected, 1, vertical)
    actual = surface(4, 4)
    _renpy.linblur(src, actual, 50, vertical)

    assert pixels(actual) == pixels(expected)


def test_pixellate_uses_the_output_width_for_horizontal_blocks():
    """Horizontal output blocks were sized by the output height, leaving gaps when the two differ."""

    src = surface(4, 2)
    src.fill((255, 0, 0, 255), (0, 0, 2, 2))
    src.fill((0, 0, 255, 255), (2, 0, 2, 2))
    dst = surface(4, 1)

    _renpy.pixellate(src, dst, 2, 2, 2, 1)

    assert pixels(dst) == [(255, 0, 0, 255)] * 2 + [(0, 0, 255, 255)] * 2


@pytest.mark.parametrize(
    ("function", "args", "message"),
    [
        (_renpy.pixellate, (surface(2, 2), surface(2, 2), 0, 1, 1, 1), "positive block sizes"),
        (_renpy.map, (surface(1, 1), surface(1, 1), bytes(256), bytes(256), bytes(256), b"\0"), "256-entry"),
        (_renpy.staticgray, (None, None, 1, 1, 1, 1, 0, bytes(256)), "pygame surface"),
    ],
    ids=["pixellate", "map", "staticgray"],
)
def test_native_helpers_reject_arguments_that_index_outside_their_buffers(function, args, message):
    with pytest.raises(Exception, match=message):
        function(*args)


def test_bidi_handles_strings_larger_than_the_stack():
    """The fribidi buffers were stack allocations, which overflowed on long strings."""

    text = "a" * 2_000_000

    assert bidi.log2vis(text)[0] == text
    assert len(bidi.get_embedding_levels(text)[0]) == len(text)
