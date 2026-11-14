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

import json
import struct

import pytest

from scripts.gltf_texture_report import read_document, report, rgba_bytes, texture_path


def model_bytes(uri):
    """Makes a minimal GLB whose image is supplied by each test."""
    data = json.dumps({"asset": {"version": "2.0"}, "images": [{"uri": uri}]}).encode()
    data += b" " * (-len(data) % 4)
    return struct.pack("<4sIIII", b"glTF", 2, 20 + len(data), len(data), 0x4E4F534A) + data


@pytest.mark.parametrize("data", [b"", b"glTF" * 5, model_bytes("image.png")[:-1]])
def test_incomplete_glb_is_rejected(data):
    """Invalid container sizes fail before a texture path can be read."""
    with pytest.raises(ValueError, match="complete"):
        read_document(data)


@pytest.mark.parametrize("uri", ["../outside.png", "%2e%2e/outside.png", "https://host/image.png", "C:/outside.png"])
def test_texture_path_cannot_escape_source(tmp_path, uri):
    """Neither encoded traversal nor absolute/remote paths can escape the source directory."""
    with pytest.raises(ValueError):
        texture_path(tmp_path / "scene.glb", uri)


def test_non_square_mip_chain():
    """A 4 by 2 map has 4 by 2, 2 by 1 and 1 by 1 levels, not a fixed 1.34 multiplier."""
    assert rgba_bytes(4, 2) == 44


def test_shared_maps_are_counted_once_without_changing_sources(tmp_path):
    """Shared day/night maps contribute one mip chain and retain every source byte."""
    Image = pytest.importorskip("PIL.Image")
    source = tmp_path / "source"
    source.mkdir()
    image = source / "shared.png"
    Image.new("RGBA", (8, 4), (64, 32, 16, 128)).save(image)
    models = [source / "day.glb", source / "night.glb"]
    for path in models:
        path.write_bytes(model_bytes("shared.png"))
    before = {p: p.read_bytes() for p in [image, *models]}
    result = report(models)
    assert len(result["resources"]) == 1
    assert result["scene_pairs"][0]["rgba_mipmap_bytes"] == rgba_bytes(8, 4)
    assert result["constant_map_savings_bytes"] == rgba_bytes(8, 4) - 4
    assert result["resources"][0]["constant"]
    assert all(path.read_bytes() == data for path, data in before.items())


def test_metadata_and_patterned_maps_are_not_changed(tmp_path):
    """Color-space metadata and nonconstant detail keep their exact original file contents."""
    Image = pytest.importorskip("PIL.Image")
    PngInfo = pytest.importorskip("PIL.PngImagePlugin").PngInfo
    source = tmp_path / "source"
    source.mkdir()
    image = Image.new("RGB", (4, 4), "red")
    image.putpixel((1, 1), (0, 255, 0))
    image.save(source / "pattern.png")
    metadata = PngInfo()
    metadata.add_text("author", "artist")
    Image.new("RGB", (4, 4), "red").save(source / "metadata.png", pnginfo=metadata)
    for name in ("pattern", "metadata"):
        (source / (name + ".glb")).write_bytes(model_bytes(name + ".png"))
    before = {p: p.read_bytes() for p in source.iterdir()}
    result = report(sorted(source.glob("*.glb")))
    assert result["constant_map_savings_bytes"] == 0
    assert all(path.read_bytes() == data for path, data in before.items())
