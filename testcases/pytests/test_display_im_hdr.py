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

import io
import math
import struct
from types import SimpleNamespace

import pytest

import renpy
from renpy.display import im


def radiance(payload, resolution=b"-Y 1 +X 8", metadata=b""):
    """Build a Radiance file without using the decoder's scanline or float conversion logic."""
    return b"#?RADIANCE\nFORMAT=32-bit_rle_rgbe\n" + metadata + b"\n" + resolution + b"\n" + payload


@pytest.mark.parametrize("encoding", ["flat", "runs", "literals", "mixed"])
def test_radiance_preserves_linear_hdr_values(encoding):
    """Flat and channel-RLE encodings retain values above one without exposure metadata changing the samples."""
    pixel = bytes((128, 64, 32, 131))
    if encoding == "flat":
        payload = pixel * 8
    else:
        payload = b"\x02\x02\x00\x08"
        for channel, value in enumerate(pixel):
            if encoding == "runs" or (encoding == "mixed" and channel % 2 == 0):
                payload += bytes((136, value))
            else:
                payload += bytes((8,)) + bytes((value,)) * 8
    image = im.load_radiance(radiance(payload, metadata=b"EXPOSURE=0.25\n"), "probe.hdr")
    assert image.get_size() == (8, 1)
    assert list(struct.iter_unpack("<4e", image.data)) == [(4, 2, 1, 1)] * 8


@pytest.mark.parametrize(
    ("resolution", "order"),
    [
        (b"-Y 2 +X 2", "ABCD"),
        (b"+Y 2 +X 2", "CDAB"),
        (b"-Y 2 -X 2", "BADC"),
        (b"+Y 2 -X 2", "DCBA"),
        (b"+X 2 -Y 2", "ACBD"),
        (b"-X 2 -Y 2", "BDAC"),
        (b"+X 2 +Y 2", "CADB"),
        (b"-X 2 +Y 2", "DBCA"),
    ],
)
def test_radiance_normalizes_orientation(resolution, order):
    """Every legal axis order and sign produces the same top-left-origin RGBA image."""
    pixels = {"A": b"\x80\0\0\x81", "B": b"\0\x80\0\x81", "C": b"\0\0\x80\x81", "D": b"\x80\x80\x80\x81"}
    image = im.load_radiance(radiance(b"".join(pixels[i] for i in order), resolution), "axes.hdr")
    assert image.get_size() == (2, 2)
    assert list(struct.iter_unpack("<4e", image.data)) == [(1, 0, 0, 1), (0, 1, 0, 1), (0, 0, 1, 1), (1, 1, 1, 1)]


def test_radiance_legacy_long_run():
    """Legacy run bytes extend the previous pixel count by powers of 256, including a zero low byte."""
    data = radiance(b"\x80\x40\x20\x83\x01\x01\x01\x00\x01\x01\x01\x01", b"-Y 1 +X 257")
    image = im.load_radiance(data, "legacy.hdr")
    assert list(struct.iter_unpack("<4e", image.data)) == [(4, 2, 1, 1)] * 257


def test_radiance_zero_exponent_is_black():
    """RGBE exponent zero encodes black regardless of the mantissa bytes."""
    image = im.load_radiance(radiance(b"\xff\xff\xff\0", b"-Y 1 +X 1"), "black.hdr")
    assert struct.unpack("<4e", image.data) == (0, 0, 0, 1)


def test_radiance_matches_python_half_rounding_for_every_representable_rgbe():
    """Native conversion equals struct's nearest-even halves, including subnormals, for exponents up to 65280."""
    payload, expected = bytearray(), bytearray()
    for exponent in range(145):
        scale = math.ldexp(1.0, exponent - 136) if exponent else 0.0
        for mantissa in range(256):
            # Never 1, 1, 1, which the flat encoding reads as a run.
            rgb = (mantissa, 255 - mantissa, mantissa // 2)
            payload += bytes((*rgb, exponent))
            expected += struct.pack("<4e", *(value * scale for value in rgb), 1.0)
    image = im.load_radiance(radiance(bytes(payload), b"-Y 145 +X 256"), "rounding.hdr")
    assert image.data == bytes(expected)


@pytest.mark.parametrize(
    ("data", "reason"),
    [
        (b"garbage\n", "signature"),
        (b"#?RADIANCE\n", "truncated header"),
        (b"#?RADIANCE\n\n-Y 1 +X 1\n", "missing RGBE"),
        (b"#?RADIANCE\nFORMAT=32-bit_rle_xyze\n\n", "only RGBE"),
        (radiance(b"", b"-Y 1 +Y 8"), "resolution axes"),
        (radiance(b"", b"-Y one +X 8"), "resolution dimensions"),
        (radiance(b"", b"-Y 0 +X 8"), "supported dimensions"),
        (radiance(b"", b"-Y 32767 +X 32767"), "supported dimensions"),
        (radiance(b"\x80"), "truncated pixel"),
        (radiance(b"\x02\x02\x00\x09"), "scanline width"),
        (radiance(b"\x02\x02\x00\x08\0"), "scanline run"),
        (radiance(b"\x02\x02\x00\x08\x89\x80"), "scanline run"),
        (radiance(b"\x02\x02\x00\x08\x08\x80"), "truncated pixel"),
        (radiance(b"\x01\x01\x01\x08"), "legacy scanline run"),
        (radiance(b"\x80\0\0\x81\x01\x01\x01\x08"), "legacy scanline run"),
        (radiance(b"\x80\0\0\x91", b"-Y 1 +X 1"), "half-float range"),
    ],
)
def test_radiance_rejects_invalid_files(data, reason):
    """Corrupt headers, oversized allocations, bad runs, and unrepresentable pixels fail with a useful error."""
    with pytest.raises(ValueError, match=reason):
        im.load_radiance(data, "broken.hdr")


@pytest.mark.parametrize(("premultiply", "srgb"), [(True, False), (False, True)])
def test_radiance_raw_texture_rejects_color_conversion(premultiply, srgb):
    """A raw HDR texture must not take the alpha-premultiplication or sRGB decoding paths."""
    texture = im.UnoptimizedTexture(im.Image("probe.hdr"), premultiply=premultiply, srgb=srgb)
    with pytest.raises(ValueError, match="premultiply=False and srgb=False"):
        texture.load()


@pytest.mark.parametrize("mipmap", [False, True, None, "auto"])
@pytest.mark.parametrize("cache_surfaces", [False, True])
def test_radiance_cache_accounts_for_float_pixels_and_mipmaps(mocker, mipmap, cache_surfaces):
    """The real image/cache path budgets eight bytes per pixel, exact mip dimensions, and retained CPU pixels."""
    data = radiance(b"\x80\x40\x20\x83" * 15, b"-Y 3 +X 5")
    mocker.patch.object(renpy.loader, "load", side_effect=lambda *args, **kwargs: io.BytesIO(data))
    mocker.patch.object(renpy.config, "cache_surfaces", cache_surfaces)
    mocker.patch.object(renpy.config, "compressed_textures", False)
    mocker.patch.object(renpy.config, "max_mipmap_level", 10)
    mocker.patch.object(renpy.config, "mipmap", "auto")
    draw = SimpleNamespace(load_texture=lambda image, **kwargs: image, mutated_surface=lambda image: None, auto_mipmap=True)
    mocker.patch.object(renpy.display, "draw", draw)
    cache = im.Cache()
    try:
        texture = im.UnoptimizedTexture(im.Image("probe.hdr"), premultiply=False, mipmap=mipmap)
        image = cache.get(texture, texture=True)
        assert struct.unpack_from("<4e", image.data) == (4, 2, 1, 1)
        gpu_bytes = 120 if mipmap is False else 144
        assert cache.get_total_size() * 4 == gpu_bytes + (120 if cache_surfaces else 0)
    finally:
        cache.quit()


def test_radiance_rejects_oversized_gpu_images_before_decoding(mocker):
    """Honor the actual renderer's single-texture limit before allocating the expanded pixel buffer."""
    loader = renpy.gl2.gl2texture.TextureLoader.__new__(renpy.gl2.gl2texture.TextureLoader)
    loader.max_texture_width = loader.max_texture_height = 2
    mocker.patch.object(renpy.display, "draw", SimpleNamespace(texture_loader=loader))
    with pytest.raises(ValueError, match="renderer's maximum texture dimensions"):
        im.load_radiance(radiance(b"\x80\x40\x20\x83" * 8), "too-wide.hdr")
