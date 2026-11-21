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

"""Texture staging verifies source isolation, gamma, alpha, and publishing boundaries."""

import json
import struct
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.gltf_texture_report import read_document
from scripts.gltf_texture_stage import resize_texture, stage

Image = pytest.importorskip("PIL.Image", reason="Texture staging requires the optional Pillow dependency.")


def fixture_model(tmp_path, images, materials=None):
    """Place source textures and a valid independent GLB under a directory that staging must leave untouched."""
    source = tmp_path / "source"
    source.mkdir()
    for name, image in images.items():
        image.save(source / name)
    document = {"asset": {"version": "2.0"}, "images": [{"uri": name} for name in images],
        "textures": [{"source": i} for i in range(len(images))], "materials": materials or []}
    payload = json.dumps(document).encode()
    payload += b" " * (-len(payload) % 4)
    path = source / "room.glb"
    path.write_bytes(struct.pack("<4sIIII", b"glTF", 2, len(payload) + 20, len(payload), 0x4E4F534A) + payload)
    return path


def test_stage_shrinks_constants_and_shares_identical_resources(tmp_path):
    """Constant maps become one pixel and aliases share a resource, while original files stay byte-identical."""
    path = fixture_model(tmp_path, {"first.png": Image.new("RGBA", (32, 16), (17, 33, 99, 128)),
        "alias.png": Image.new("RGBA", (32, 16), (17, 33, 99, 128))})
    before = {p.name: p.read_bytes() for p in path.parent.iterdir()}
    output = tmp_path / "staged"
    result = stage(path, output)
    document = read_document((output / path.name).read_bytes())
    assert result["unique_resources"] == 1
    assert document["images"][0]["uri"] == document["images"][1]["uri"]
    with Image.open(output / document["images"][0]["uri"]) as texture:
        assert texture.size == (1, 1) and texture.getpixel((0, 0)) == (17, 33, 99, 128)
    assert {p.name: p.read_bytes() for p in path.parent.iterdir()} == before


@pytest.mark.parametrize("color,expected", [(False, (128, 128, 128)), (True, (188, 188, 188))])
def test_resize_distinguishes_linear_data_from_srgb(color, expected):
    """A black/white average is 128 for data and about 188 when sRGB colors are filtered in linear light."""
    source = Image.new("RGB", (2, 1))
    source.putdata([(0, 0, 0), (255, 255, 255)])
    result = resize_texture(source, (1, 1), color)
    assert result.getpixel((0, 0)) == expected


def test_color_resize_does_not_bleed_hidden_rgb():
    """Transparent red must not contaminate blue when colors are filtered with premultiplied linear alpha."""
    source = Image.new("RGBA", (2, 1))
    source.putdata([(255, 0, 0, 0), (0, 0, 255, 255)])
    assert resize_texture(source, (1, 1), True).getpixel((0, 0)) == (0, 0, 255, 128)


def test_stage_rejects_ambiguous_resizing_without_publishing(tmp_path):
    """An image used for both color and normal data needs separate variants before lossy resizing is safe."""
    image = Image.new("RGB", (2, 1))
    image.putdata([(0, 0, 0), (255, 255, 255)])
    path = fixture_model(tmp_path, {"mixed.png": image}, [{"normalTexture": {"index": 0},
        "pbrMetallicRoughness": {"baseColorTexture": {"index": 0}}}])
    with pytest.raises(ValueError, match="unambiguous"):
        stage(path, tmp_path / "output", max_size=1)
    assert not (tmp_path / "output").exists()


@pytest.mark.parametrize("destination", ["source/staged", "existing"])
def test_stage_cannot_write_into_source_or_existing_directory(tmp_path, destination):
    """Publishing never overwrites an existing directory or adds files beside the bake's inputs."""
    path = fixture_model(tmp_path, {"map.png": Image.new("RGB", (4, 4), "white")})
    (tmp_path / "existing").mkdir()
    with pytest.raises(ValueError, match="new directory"):
        stage(path, tmp_path / destination)


def test_stage_runs_as_a_file():
    """The script runs directly as well as with python -m, since scripts/ need not be importable."""
    script = Path(__file__).parents[1] / "gltf_texture_stage.py"
    result = subprocess.run([sys.executable, str(script), "--help"], check=False, capture_output=True,
        text=True, cwd=script.parent)
    assert result.returncode == 0, result.stderr
