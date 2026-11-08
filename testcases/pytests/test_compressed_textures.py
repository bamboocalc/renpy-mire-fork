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
import struct
from types import SimpleNamespace

import pytest

import renpy
from renpy.display import im


def make_ktx2(levels=(b"\x01" * 32, b"\x02" * 16), vk_format=145, premultiplied=True, scheme=0, width=8, height=4):
    """
    Returns a KTX2 file with the given level data, largest first, and a BC7 data format descriptor.
    """

    block = struct.pack("<IHHBBBB4B8B", 0, 2, 40, 134, 1, 1, int(premultiplied), 3, 3, 0, 0, 16, 0, 0, 0, 0, 0, 0, 0)
    sample = struct.pack("<HBB4BII", 0, 127, 0, 0, 0, 0, 0, 0, 0xFFFFFFFF)
    dfd = struct.pack("<I", 4 + len(block) + len(sample)) + block + sample

    dfd_offset = 80 + 24 * len(levels)
    offset = dfd_offset + len(dfd)
    index = b""
    data = b""

    for level in levels:
        index += struct.pack("<3Q", offset + len(data), len(level), len(level))
        data += level

    header = im.KTX2_IDENTIFIER + struct.pack("<9I", vk_format, 1, width, height, 0, 0, 1, len(levels), scheme)
    header += struct.pack("<4I2Q", dfd_offset, len(dfd), 0, 0, 0, 0)

    return header + index + dfd + data


@pytest.fixture
def gpu(mocker):
    """A renderer that can upload BC7, and a loader holding one texture with a KTX2 sibling."""

    files = {"tex.ktx2": make_ktx2()}
    loader = SimpleNamespace(compressed_formats={145, 146})
    mocker.patch.object(renpy.display, "draw", SimpleNamespace(texture_loader=loader, info={"max_texture_size": 4096}))
    mocker.patch.object(renpy.config, "compressed_textures", True)
    mocker.patch.object(renpy.loader, "loadable", side_effect=lambda fn, **kwargs: fn in files)
    mocker.patch.object(renpy.loader, "load", side_effect=lambda fn, **kwargs: io.BytesIO(files[fn]))
    mocker.patch.object(im.Image, "load", return_value="decoded png")
    return SimpleNamespace(files=files, loader=loader)


def test_ktx2_levels_and_premultiplied_flag():
    """Levels are read through the level index, and bit 0 of the descriptor flags marks premultiplied alpha."""

    rv = im.load_ktx2(make_ktx2(premultiplied=False), "tex.ktx2")

    assert (rv.vk_format, rv.get_size(), rv.premultiplied) == (145, (8, 4), False)
    assert [rv.data[offset : offset + length] for offset, length in rv.levels] == [b"\x01" * 32, b"\x02" * 16]
    assert rv.compressed_size == 48


@pytest.mark.parametrize(
    ("data", "message"),
    [
        (b"PNG" + make_ktx2()[3:], "not a KTX2 file"),
        (make_ktx2(scheme=2), "no supercompression"),
        (make_ktx2()[:-1], "truncated"),
    ],
    ids=["identifier", "supercompression", "truncated"],
)
def test_ktx2_rejects_unsupported_files(data, message):
    with pytest.raises(Exception, match=message):
        im.load_ktx2(data, "tex.ktx2")


def test_raw_texture_uses_supported_sibling(gpu):
    """The compressed sibling replaces the image file only while the setting is on."""

    texture = im.UnoptimizedTexture(im.Image("tex.png"))

    assert isinstance(texture.load(), im.CompressedImage)
    renpy.config.compressed_textures = False
    assert texture.load() == "decoded png"


@pytest.mark.parametrize("case", ["no-sibling", "no-bc7", "other-format", "too-large"])
def test_raw_texture_falls_back_to_the_image_file(gpu, case):
    """Platforms and files the GPU can't use keep loading the image file."""

    if case == "no-sibling":
        gpu.files.clear()
    elif case == "no-bc7":
        gpu.loader.compressed_formats = set()
    elif case == "other-format":
        gpu.files["tex.ktx2"] = make_ktx2(vk_format=152)
    else:
        renpy.display.draw.info["max_texture_size"] = 4

    assert im.UnoptimizedTexture(im.Image("tex.png")).load() == "decoded png"


def test_raw_texture_rejects_mismatched_alpha(gpu):
    """A straight-alpha texture can't use a file whose colors were premultiplied."""

    with pytest.raises(Exception, match="premultiplied alpha"):
        im.UnoptimizedTexture(im.Image("tex.png"), premultiply=False).load()


def test_cache_counts_compressed_bytes(gpu, mocker):
    """The image cache counts compressed data in its four-byte pixel units, not the decoded size."""

    mocker.patch.object(renpy.config, "cache_surfaces", False)
    image = im.load_ktx2(make_ktx2(), "tex.ktx2")

    assert im.CacheEntry(None, image, (0, 0, 8, 4)).size() == 48 // 4
