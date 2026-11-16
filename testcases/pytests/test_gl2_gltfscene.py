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

import base64
import json
import math
import struct
from pathlib import Path

import pytest

from renpy.display.matrix import Matrix
from renpy.gl2 import gltfscene


@pytest.fixture
def document():
    """Use a redistributable scene with a nested light, repeated geometry, and two real animation clips."""
    path = Path(__file__).parents[1] / "game/models/scene-controls.gltf"
    return json.loads(path.read_text(encoding="utf-8"))


def set_channel(document, path, times, values, mode="LINEAR", component=5126):
    """Replace only animation buffers, retaining the fixture's scene graph and real geometry."""
    data = bytearray(base64.b64decode(document["buffers"][0]["uri"].partition(",")[2]))
    indices = []
    for rows, kind, component_type in (([(t,) for t in times], "SCALAR", 5126),
            (values, "VEC4" if path == "rotation" else "VEC3", component)):
        offset = len(data)
        formats = {5120: "b", 5121: "B", 5122: "h", 5123: "H", 5126: "f"}
        flat = [value for row in rows for value in row]
        data.extend(struct.pack("<" + formats[component_type] * len(flat), *flat))
        document["bufferViews"].append({"buffer": 0, "byteOffset": offset, "byteLength": len(data) - offset})
        indices.append(len(document["accessors"]))
        document["accessors"].append({"bufferView": len(document["bufferViews"]) - 1,
            "type": kind, "componentType": component_type, "count": len(rows),
            "normalized": component_type != 5126})
    document["buffers"][0] = {"byteLength": len(data),
        "uri": "data:application/octet-stream;base64," + base64.b64encode(data).decode()}
    document["animations"] = [{"name": "Test", "samplers": [
        {"input": indices[0], "output": indices[1], "interpolation": mode}],
        "channels": [{"sampler": 0, "target": {"node": 1, "path": path}}]}]


def pose(document, time=0, overrides=None, loop=False, animation="Slide"):
    """Return the posed door matrix and subtree visibility in Ren'Py scene coordinates."""
    asset = gltfscene.Asset(document, "scene.gltf")
    matrices, visible = asset.pose(gltfscene.node_overrides(overrides), animation, time, loop, 100)
    return matrices[1], visible


def test_scene_info_preserves_names_and_hierarchy(document):
    """Scripts can discover stable source indices, ancestry, materials, and clip durations without touching shared data."""
    asset = gltfscene.Asset(document, "scene.gltf")
    info = asset.info()
    assert info["nodes"][1] == {"index": 1, "name": "Door", "parent": 0,
        "children": (3,), "mesh": 0, "camera": None, "active": True}
    assert info["materials"][0]["name"] == "DoorPaint"
    assert info["animations"][0] == {"index": 0, "name": "Slide", "duration": 2, "channels": 1}
    info["nodes"][1]["name"] = "Changed"
    assert asset.info()["nodes"][1]["name"] == "Door"


def test_cached_bind_pose_is_independent_of_previous_instances(document):
    """Reusing static transforms cannot leak an animated parent's pose or hidden subtree into another instance."""
    asset = gltfscene.Asset(document, "scene.gltf")
    original, visibility = asset.pose((), None, 0, False, 100)
    moved, hidden = asset.pose(gltfscene.node_overrides({"Room": {"translation": (1, 0, 0), "visible": False}}),
        "Slide", 1, False, 100)
    restored, visible = asset.pose((), None, 0, False, 100)
    assert moved[1].transform(0, 0, 0, components=3) == pytest.approx((100, 0, 20))
    assert not hidden[1] and visible[1] and visibility[1]
    assert restored[1].transform(0, 0, 0, components=3) == original[1].transform(0, 0, 0, components=3)
    restored.pop(1)
    again, _ = asset.pose((), None, 0, False, 50)
    assert again[1].transform(0, 0, 0, components=3) == pytest.approx((-30, 0, 10))


@pytest.mark.parametrize("kind", ["perspective", "orthographic", "infinite"])
def test_camera_projects_authored_pose_and_clip_distances(kind):
    """Camera node scale does not change field of view, and source-unit near/far distances follow model zoom."""
    record = {"type": "orthographic", "orthographic": {"xmag": 2, "ymag": 1.5, "znear": 0, "zfar": 20}}
    if kind != "orthographic":
        record = {"type": "perspective", "perspective": {"yfov": 2 * math.atan(0.3), "znear": 0.1}}
        if kind != "infinite":
            record["perspective"]["zfar"] = 20
    definition = gltfscene.camera_definitions({"cameras": [record]})[0]
    world = Matrix.scale(100, -100, 100) * Matrix.offset(1, 2, 5) * Matrix.scale(2, 3, 4)
    view, projection, viewport = gltfscene.camera_matrices(definition, world, 400, 300, 100)
    transform = projection * view
    x, y, z, w = transform.transform(100, -200, 0, components=4)
    assert (x / w, y / w) == pytest.approx((200, 150))
    assert viewport == pytest.approx((400, 300))
    for distance, expected in ((definition["znear"] * 100, -1), (2000, 1)):
        if kind == "infinite" and expected == 1:
            continue
        _x, _y, z, w = transform.transform(100, -200, 500 - distance, components=4)
        assert z / w == pytest.approx(expected, abs=1e-5)


def test_camera_selection_disambiguates_instances_and_inherits_nodes(document):
    """Definition names select unique active instances; a shared definition needs an explicit node selector."""
    document["cameras"] = [{"name": "Main", "type": "perspective", "perspective": {"yfov": 1, "znear": 0.1}}]
    document["nodes"].extend([{"name": "FirstCamera", "camera": 0}, {"name": "SecondCamera", "camera": 0}])
    document["scenes"][0]["nodes"].extend([4, 5])
    asset = gltfscene.Asset(document, "room.gltf")
    assert asset.camera_node() == 4
    assert asset.camera_node("Main", "SecondCamera") == 5
    assert asset.info()["cameras"][0]["nodes"] == (4, 5)
    with pytest.raises(ValueError, match="instanced"):
        asset.camera_node("Main")


@pytest.mark.parametrize("values", [{"yfov": 0, "znear": 1}, {"yfov": math.pi, "znear": 1},
    {"yfov": 1, "znear": 0}, {"yfov": 1, "znear": 1, "zfar": 1},
    {"yfov": 1, "znear": 1, "aspectRatio": 0}, {"yfov": math.nan, "znear": 1}])
def test_camera_rejects_invalid_perspective(values):
    """Malformed projection values fail before singular matrices or invalid clip planes reach native rendering."""
    with pytest.raises(ValueError):
        gltfscene.camera_definitions({"cameras": [{"type": "perspective", "perspective": values}]})


def test_orthographic_camera_preserves_mirrored_magnification():
    """Nonzero negative orthographic magnification mirrors the view while preserving a valid positive viewport."""
    definition = gltfscene.camera_definitions({"cameras": [{"type": "orthographic",
        "orthographic": {"xmag": -2, "ymag": 1.5, "znear": 0, "zfar": 20}}]})[0]
    view, projection, viewport = gltfscene.camera_matrices(definition, Matrix.scale(100, -100, 100), 400, 300, 100)
    x, _y, _z, w = (projection * view).transform(100, 0, -500, components=4)
    assert x / w == pytest.approx(100)
    assert viewport == pytest.approx((400, 300))


@pytest.mark.parametrize(("time", "loop", "x"), [(-1, False, -60), (0, False, -60),
    (1, False, 0), (2, False, 60), (3, False, 60), (3, True, 0), (-1, True, 0)])
def test_linear_playback_clamps_or_loops(document, time, loop, x):
    """The shown-time clock seeks deterministically in both directions, holding the last pose unless looping is requested."""
    matrix, _visible = pose(document, time, loop=loop)
    assert matrix.transform(0, 0, 0, components=3) == pytest.approx((x, 0, 20), abs=1e-5)


def test_override_replaces_one_animated_component_and_inherits_visibility(document):
    """An instance can override a clip's translation and hide an entire subtree without changing shared bind transforms."""
    matrix, visible = pose(document, 1, {"Door": {"translation": (2, 3, 4), "visible": False}})
    assert matrix.transform(0, 0, 0, components=3) == pytest.approx((200, -300, 400))
    assert not visible[1] and not visible[3] and visible[2]
    assert document["nodes"][1]["translation"] == [-0.6, 0, 0.2]


def test_duplicate_names_require_numeric_indices(document):
    """Repeated exporter names are not silently attached to the first matching object."""
    document["nodes"][2]["name"] = "Door"
    with pytest.raises(ValueError, match="ambiguous"):
        pose(document, overrides={"Door": {"visible": False}})
    _matrix, visible = pose(document, overrides={1: {"visible": False}})
    assert not visible[1] and visible[2]


def test_step_animation_holds_until_next_key(document):
    """STEP interpolation jumps at the exact timestamp and remains constant between keys."""
    set_channel(document, "translation", [0, 1, 2], [(0, 0, 0), (1, 0, 0), (4, 0, 0)], "STEP")
    before, _ = pose(document, 0.999, animation="Test")
    after, _ = pose(document, 1, animation="Test")
    assert before.transform(0, 0, 0, components=3)[0] == 0
    assert after.transform(0, 0, 0, components=3)[0] == 100


@pytest.mark.parametrize("opposite", [False, True])
def test_rotation_uses_shortest_spherical_path(document, opposite):
    """Equivalent quaternion signs produce the same orientation; interpolation must not pass through a zero quaternion."""
    q = (0, math.sin(math.pi / 4), 0, math.cos(math.pi / 4))
    set_channel(document, "rotation", [0, 2], [(0, 0, 0, 1), tuple(-v for v in q) if opposite else q])
    matrix, _visible = pose(document, 1, animation="Test")
    assert matrix.transform(0, 0, -1, w=0, components=3) == pytest.approx(
        (-math.sqrt(0.5) * 100, 0, -math.sqrt(0.5) * 100), abs=1e-4)


def test_cubic_tangents_scale_by_segment_duration(document):
    """The Hermite tangent is a derivative in seconds, so a two-second segment changes its contribution."""
    set_channel(document, "translation", [0, 2], [(0, 0, 0), (0, 0, 0), (2, 0, 0),
        (0, 0, 0), (1, 0, 0), (0, 0, 0)], "CUBICSPLINE")
    matrix, _visible = pose(document, 1, animation="Test")
    assert matrix.transform(0, 0, 0, components=3)[0] == 100


def test_cubic_rotation_is_normalized(document):
    """Hermite component interpolation produces a quaternion that must be normalized before constructing the transform."""
    set_channel(document, "rotation", [0, 2], [(0, 0, 0, 0), (0, 0, 0, 1), (0, 0, 0, 0),
        (0, 0, 0, 0), (0, 1, 0, 0), (0, 0, 0, 0)], "CUBICSPLINE")
    matrix, _visible = pose(document, 1, animation="Test")
    assert matrix.transform(0, 0, -1, w=0, components=3) == pytest.approx((-100, 0, 0), abs=1e-5)


@pytest.mark.parametrize("component", [5120, 5121, 5122, 5123])
def test_normalized_integer_rotation_keys(document, component):
    """Core glTF permits normalized integer rotation output; those values need decoding before quaternion validation."""
    maximum = {5120: 127, 5121: 255, 5122: 32767, 5123: 65535}[component]
    set_channel(document, "rotation", [0, 2], [(0, 0, 0, maximum), (0, maximum, 0, 0)], component=component)
    matrix, _visible = pose(document, 1, animation="Test")
    assert matrix.transform(0, 0, -1, w=0, components=3) == pytest.approx((-100, 0, 0), abs=1e-5)


@pytest.mark.parametrize("component", [5120, 5121])
def test_quantized_rotation_accepts_precision_error(document, component):
    """An eight-bit 90-degree rotation cannot encode a perfectly unit quaternion and must be renormalized."""
    maximum = 127 if component == 5120 else 255
    diagonal = round(math.sqrt(0.5) * maximum)
    set_channel(document, "rotation", [0, 2], [(0, diagonal, 0, diagonal), (0, maximum, 0, 0)], component=component)
    matrix, _visible = pose(document, 0, animation="Test")
    assert matrix.transform(0, 0, -1, w=0, components=3) == pytest.approx((-100, 0, 0), abs=1e-5)


def test_sparse_animation_overlays_implicit_zero_values(document):
    """Sparse keyframes may start from an implicit zero buffer, with replacements at strictly increasing indices."""
    set_channel(document, "translation", [0, 1, 2], [(0, 0, 0), (0, 0, 0), (2, 0, 0)])
    output = document["animations"][0]["samplers"][0]["output"]
    values = document["accessors"][output]
    view = document["bufferViews"][values.pop("bufferView")]
    view["byteOffset"] += 24
    view["byteLength"] = 12
    data = base64.b64decode(document["buffers"][0]["uri"].partition(",")[2]) + b"\x02"
    document["bufferViews"].append({"buffer": 0, "byteOffset": len(data) - 1, "byteLength": 1})
    values["sparse"] = {"count": 1, "indices": {"bufferView": len(document["bufferViews"]) - 1,
        "componentType": 5121}, "values": {"bufferView": document["bufferViews"].index(view)}}
    document["buffers"][0] = {"byteLength": len(data),
        "uri": "data:application/octet-stream;base64," + base64.b64encode(data).decode()}
    matrix, _visible = pose(document, 1.5, animation="Test")
    assert matrix.transform(0, 0, 0, components=3)[0] == 100


def test_interleaved_animation_uses_accessor_stride(document):
    """Animation values can share a buffer view with padding; padding must not become transform components."""
    set_channel(document, "translation", [0, 2], [(0, 0, 0), (2, 0, 0)])
    output = document["animations"][0]["samplers"][0]["output"]
    view = document["bufferViews"][document["accessors"][output]["bufferView"]]
    data = base64.b64decode(document["buffers"][0]["uri"].partition(",")[2])
    data = data[:view["byteOffset"]] + struct.pack("<8f", 0, 0, 0, 999, 2, 0, 0, 999)
    view.update(byteStride=16, byteLength=32)
    document["buffers"][0] = {"byteLength": len(data),
        "uri": "data:application/octet-stream;base64," + base64.b64encode(data).decode()}
    matrix, _visible = pose(document, 1, animation="Test")
    assert matrix.transform(0, 0, 0, components=3)[0] == 100


@pytest.mark.parametrize("case", ["timestamps", "nan", "count", "bounds", "node", "duplicate", "matrix", "quaternion"])
def test_invalid_animation_data_fails_before_native_import(document, case):
    """Malformed channels fail explicitly instead of moving the wrong object or sending undefined values to the renderer."""
    if case == "timestamps":
        set_channel(document, "translation", [1, 0], [(0, 0, 0), (1, 0, 0)])
    elif case == "nan":
        set_channel(document, "translation", [0, 1], [(0, 0, 0), (math.nan, 0, 0)])
    elif case == "count":
        set_channel(document, "translation", [0, 1], [(0, 0, 0)])
    elif case == "bounds":
        document["accessors"][-1]["byteOffset"] = 100000
    elif case == "node":
        document["animations"][0]["channels"][0]["target"]["node"] = 100
    elif case == "duplicate":
        document["animations"][0]["channels"] *= 2
    elif case == "matrix":
        document["nodes"][1] = {"matrix": [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]}
    else:
        set_channel(document, "rotation", [0, 1], [(0, 0, 0, 1), (0, 0, 0, 0)])
    with pytest.raises(ValueError):
        gltfscene.Asset(document, "scene.gltf")


def test_asset_diagnostics_distinguish_optional_and_required_extensions(document):
    """Optional visual differences are reported; required functionality must never be silently ignored."""
    document["extensionsUsed"].append("KHR_materials_iridescence")
    warnings = gltfscene.Asset(document, "scene.gltf").info()["warnings"]
    assert any("iridescence" in warning for warning in warnings)
    document["extensionsRequired"] = ["KHR_materials_iridescence"]
    with pytest.raises(ValueError, match="required.*iridescence"):
        gltfscene.Asset(document, "scene.gltf")
