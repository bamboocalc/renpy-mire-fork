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


import hashlib

import pytest

from renpy import pygame


@pytest.mark.parametrize("alpha", [0, 1, 128, 254, 255])
@pytest.mark.parametrize("subsurface", [False, True])
@pytest.mark.parametrize(
    "primitive, counts",
    [
        (
            "circle",
            [
                "000000000",
                "000010000",
                "001101100",
                "010000010",
                "020000020",
                "010000010",
                "001101100",
                "000010000",
                "000000000",
            ],
        ),
        (
            "filled_circle",
            [
                "000000000",
                "000010000",
                "001111100",
                "011111110",
                "011111110",
                "011111110",
                "001111100",
                "000010000",
                "000000000",
            ],
        ),
    ],
)
def test_circle_preserves_point_coverage_and_overdraw(primitive, counts, alpha, subsurface):
    surfaces = []
    for _ in range(2):
        surface = pygame.Surface((15, 15) if subsurface else (9, 9), pygame.SRCALPHA, 32)
        if subsurface:
            surface = surface.subsurface((2, 3, 9, 9))
        surface.fill((71, 93, 115, 128))
        surface.set_clip((1, 2, 7, 6))
        surfaces.append(surface)
    actual, expected = surfaces
    color = (19, 117, 243, alpha)
    getattr(pygame.gfxdraw, primitive)(actual, 4, 4, 3, color)
    # The original outline draws its horizontal endpoints twice.
    for y, row in enumerate(counts):
        for x, count in enumerate(row):
            for _ in range(int(count)):
                pygame.gfxdraw.pixel(expected, x, y, color)
    assert [tuple(actual.get_at((x, y))) for y in range(9) for x in range(9)] == [
        tuple(expected.get_at((x, y))) for y in range(9) for x in range(9)
    ]


def test_circle_flushes_full_and_partial_point_batches():
    surface = pygame.Surface((129, 129), pygame.SRCALPHA, 32)
    surface.fill((71, 93, 115, 128))
    surface.set_clip((1, 2, 126, 124))
    pygame.gfxdraw.circle(surface, 64.25, 63.75, 63, (19, 117, 243, 255))
    pixels = bytes(channel for y in range(129) for x in range(129) for channel in surface.get_at((x, y)))
    assert hashlib.sha256(pixels).hexdigest() == "67ea5702c32c1b8f55ebd5631bbcd72848276234e96937603b39d21d076891ae"
