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
import collections
import json
import math
import pickle
import struct
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import pytest

import renpy
from renpy.display.render import IDENTITY, Render
from renpy.gl2 import assimp


@pytest.fixture
def model_cache(mocker):
    """Isolate imported models and prediction without starting the interaction loop."""
    cache = collections.OrderedDict()
    mocker.patch.object(assimp, "cache", cache)
    mocker.patch.object(assimp, "predicted", set())
    mocker.patch.object(renpy.config, "gltf_model_cache_size_mb", 0)
    return cache


@pytest.mark.parametrize(
    ("shader", "inherited", "expected"),
    [
        ("example.model", ("renpy.matrixcolor",), ("renpy.matrixcolor", "example.model")),
        (
            ("example.surface", "example.material"),
            ("renpy.matrixcolor", "renpy.alpha"),
            ("renpy.matrixcolor", "renpy.alpha", "example.surface", "example.material"),
        ),
    ],
    ids=["single-part", "multi-part"],
)
def test_gltf_predicts_its_shader_without_importing(mocker, shader, inherited, expected):
    """Cold prediction combines every parent and model shader part without loading geometry."""
    predict = mocker.patch.object(renpy.gl2.gl2shadercache, "predict_shader")
    load = mocker.patch.object(assimp.GLTFModel, "load")
    model = assimp.GLTFModel("unread.glb", shader=shader)
    assert model.predict_shaders(inherited) == ()
    predict.assert_called_once_with(expected)
    load.assert_not_called()


@pytest.mark.parametrize(
    ("sizes_mib", "budget_mib", "survivors"),
    [
        ((3, 3), 4, (1,)),
        ((2, 2, 2), 2, (2,)),
        ((8,), 4, ()),
        ((2, 2), 4, (0, 1)),
    ],
    ids=["oldest-one", "multiple-evictions", "oversized", "exact-fit"],
)
def test_inactive_models_keep_only_the_most_recent_that_fit(model_cache, mocker, sizes_mib, budget_mib, survivors):
    """Inactive retention fits its budget by dropping old entries, including an oversized last entry."""
    models = [object() for _ in sizes_mib]
    for model, size in zip(models, sizes_mib):
        model_cache[model] = SimpleNamespace(memory_size=size * 1024 * 1024)
    mocker.patch.object(renpy.config, "gltf_model_cache_size_mb", budget_mib)
    assimp.preload()
    assert list(model_cache) == [models[index] for index in survivors]


def test_zero_budget_preserves_existing_eviction_policy(model_cache, mocker):
    """With retention disabled, inactive models are discarded and predicted models remain."""
    mocker.patch.object(assimp.GLTFModel, "_prepared", return_value=({}, {}, {}, []))
    old = object()
    active = assimp.GLTFModel("active.glb", shader="renpy.texture")
    model_cache[old] = SimpleNamespace(memory_size=1)
    model_cache[active.cache_key] = SimpleNamespace(memory_size=1)
    assimp.predicted.add(active)
    assimp.preload()
    assert list(model_cache) == [active.cache_key]


def test_loading_an_inactive_model_promotes_it_before_eviction(model_cache, mocker):
    """Revisiting an older room retains it instead of a room that has not been used as recently."""
    older = assimp.GLTFModel("older.glb", shader="renpy.texture")
    recent = assimp.GLTFModel("recent.glb", shader="renpy.texture")
    for model in (older, recent):
        model_cache[model.cache_key] = SimpleNamespace(memory_size=3 * 1024 * 1024)
    mocker.patch.object(renpy.config, "gltf_model_cache_size_mb", 4)
    data = model_cache[older.cache_key]
    assert older.load() is data
    assimp.preload()
    assert list(model_cache) == [older.cache_key]
    assert model_cache[older.cache_key] is data


def test_predicted_models_do_not_consume_the_inactive_budget(model_cache, mocker):
    """A predicted room larger than the budget does not evict an inactive room that fits."""
    inactive = object()
    active = assimp.GLTFModel("active.glb", shader="renpy.texture")
    model_cache[inactive] = SimpleNamespace(memory_size=3 * 1024 * 1024)
    model_cache[active.cache_key] = SimpleNamespace(memory_size=8 * 1024 * 1024)
    assimp.predicted.add(active)
    mocker.patch.object(assimp.GLTFModel, "_prepared", return_value=({}, {}, {}, []))
    mocker.patch.object(renpy.config, "gltf_model_cache_size_mb", 4)
    assimp.preload()
    assert set(model_cache) == {inactive, active.cache_key}


def test_instances_sharing_an_import_each_preload_their_textures(model_cache, mocker):
    """The second instance's cache hit still queues its own override, though the first instance imported."""
    mesh_info = assimp.MeshInfo.__new__(assimp.MeshInfo)
    mesh_info.texture_uniforms, mesh_info.material_index = {}, 0
    loader = SimpleNamespace(load=mocker.Mock(side_effect=lambda data, *args: setattr(data, "mesh_info", [mesh_info])))
    mocker.patch.object(assimp, "loader", loader)
    preload = mocker.patch.object(renpy.display.im.cache, "preload_image")
    models = [assimp.GLTFModel("room.glb", shader="renpy.texture", u_tex_diffuse=renpy.display.im.Null(color))
        for color in ("#f00", "#0f0")]
    assimp.predicted.update(models)
    assimp.preload()
    loader.load.assert_called_once()
    assert {call.args[0] for call in preload.call_args_list} == {m.texture_uniforms["u_tex_diffuse"] for m in models}


def test_duplicate_of_a_rendered_prototype_starts_its_clip_at_zero():
    """An image's prototype rendered for three seconds does not start later shows three seconds in."""
    prototype = assimp.GLTFModel("room.glb", scene=True, animation="Slide")
    prototype._animation_elapsed = 3.0
    shown = prototype._duplicate(None)
    assert shown is not prototype
    assert (shown._animation_resume, shown._animation_rebase) == (0.0, True)


def test_repeated_prediction_then_departure_releases_model(model_cache, mocker):
    """An identical prediction pass must still discard its accumulator before the next room."""
    model = assimp.GLTFModel("departed.glb", shader="renpy.texture")
    model_cache[model.cache_key] = SimpleNamespace(memory_size=1)
    mocker.patch.object(assimp, "new_predicted", set())
    mocker.patch.object(renpy.display.im.cache, "start_prediction")
    for _ in range(2):
        assimp.new_predicted.add(model)
        assimp.finish_predict()
    assimp.finish_predict()
    assimp.preload()
    assert model.cache_key not in model_cache


def test_explicit_memory_clear_removes_predicted_and_retained_models(model_cache, mocker):
    """An explicit clear overrides both prediction and the optional retention budget."""
    inactive = object()
    active = assimp.GLTFModel("active.glb", shader="renpy.texture")
    model_cache[inactive] = SimpleNamespace(memory_size=1)
    model_cache[active.cache_key] = SimpleNamespace(memory_size=1)
    assimp.predicted.add(active)
    mocker.patch.object(renpy.config, "gltf_model_cache_size_mb", 4)
    assimp.free_memory()
    assert not model_cache


@pytest.mark.parametrize("second_request", ["load", "clear"])
def test_import_serializes_duplicate_requests_and_memory_clear(model_cache, mocker, second_request):
    """Concurrent imports publish once, and a waiting clear removes the completed import."""
    entered, waiting, finish = threading.Event(), threading.Event(), threading.Event()
    native_lock = threading.Lock()

    class ObservedImportLock:
        """Expose contention after a cold cache check while the first import is still running."""

        def __enter__(self):
            if entered.is_set():
                waiting.set()
            return native_lock.__enter__()

        def __exit__(self, *args):
            return native_lock.__exit__(*args)

    mocker.patch.object(assimp, "loader_lock", ObservedImportLock())

    def load(data, *args):
        data.mesh_info = []
        entered.set()
        assert finish.wait(5)

    loader = SimpleNamespace(load=mocker.Mock(side_effect=load))
    mocker.patch.object(assimp, "loader", loader)
    model = assimp.GLTFModel("shared.glb", shader="renpy.texture")
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(model.load)
        try:
            assert entered.wait(3)
            second = pool.submit(model.load if second_request == "load" else assimp.free_memory)
            assert waiting.wait(3)
        finally:
            finish.set()
        first_data = first.result(timeout=3)
        second_data = second.result(timeout=3)
        if second_request == "load":
            assert first_data is second_data
        else:
            assert not model_cache
    loader.load.assert_called_once()


def test_failed_import_can_be_retried(model_cache, mocker):
    """An import error leaves neither a cached partial model nor a locked loader."""
    loader = SimpleNamespace(load=mocker.Mock(side_effect=ValueError("bad model")))
    mocker.patch.object(assimp, "loader", loader)
    model = assimp.GLTFModel("broken.glb", shader="renpy.texture")
    with pytest.raises(ValueError, match="bad model"):
        model.load()
    assert model.cache_key not in model_cache
    loader.load.side_effect = lambda data, *args: setattr(data, "mesh_info", [])
    assert model.load() is model_cache[model.cache_key]


def test_equal_import_options_share_one_import(model_cache, mocker):
    """Models that differ only in shaders share an import; zoom and tangents change the imported data."""
    loader = SimpleNamespace(load=mocker.Mock(side_effect=lambda data, *args: setattr(data, "mesh_info", [])))
    mocker.patch.object(assimp, "loader", loader)
    first = assimp.GLTFModel("room.glb", shader="example.first")
    second = assimp.GLTFModel("room.glb", shader="example.second")
    assert first.load() is second.load()
    loader.load.assert_called_once()
    assimp.GLTFModel("room.glb", shader="example.first", zoom=2.0).load()
    assimp.GLTFModel("room.glb", shader="example.first", tangents=True).load()
    assert loader.load.call_count == 3


def test_shared_import_renders_with_each_models_shaders(model_cache, mocker):
    """A shared import draws with the shaders of the model being rendered, not the one that imported it."""
    mocker.patch.object(assimp, "new_predicted", set())
    mocker.patch.object(renpy.display.im, "render_for_texture", return_value=Render(1, 1))
    mesh_info = assimp.MeshInfo.__new__(assimp.MeshInfo)
    mesh_info.mesh, mesh_info.uniforms, mesh_info.texture_uniforms = None, {}, {}
    mesh_info.material_index, mesh_info.twosided = 0, False
    first = assimp.GLTFModel("room.glb", shader="example.first")
    second = assimp.GLTFModel("room.glb", shader="example.second")
    data = assimp.ModelData()
    data.blit_info = [SimpleNamespace(mesh_info=mesh_info, reverse=IDENTITY, forward=IDENTITY)]
    data.mesh_info = [mesh_info]
    model_cache[first.cache_key] = data
    for model, shader in ((first, "example.first"), (second, "example.second")):
        mesh_render = model.render(0, 0, 0, 0).children[0][0]
        assert mesh_render.shaders == (shader,)


def _native_attribute_rows(mesh):
    """Read native attributes through the diagnostic repr; Mesh has no Python attribute getter or serializer."""
    rows = [
        tuple(float(value) for value in line.split("|", 1)[1].split())
        for line in repr(mesh).splitlines()
        if "|" in line
    ]
    assert len(rows) == mesh.points
    return rows


@pytest.mark.parametrize("authored_tangents", [False, True], ids=["generated", "authored"])
@pytest.mark.parametrize("standard", [False, True], ids=["legacy", "standard-material"])
def test_native_import_keeps_geometry_with_and_without_tangents(
    model_cache, mocker, tmp_path, authored_tangents, standard
):
    """Preserve mirrored UV seams and authored tangents, generating missing tangents only when requested.

    Axis-aligned vectors and UVs remain distinguishable in the three-decimal diagnostic repr.
    """
    positions = (0, 0, 0, 1, 0, 0, 1, 1, 0, 0, 0, 0, 1, 1, 0, 0, 1, 0)
    streams = [
        (struct.pack("<18f", *positions), "VEC3", 6, 5126),
        (struct.pack("<18f", *((0, 0, 1) * 6)), "VEC3", 6, 5126),
        (struct.pack("<12f", 0, 0, 1, 0, 1, 1, 1, 0, 0, 1, 1, 1), "VEC2", 6, 5126),
        (struct.pack("<6H", 0, 1, 2, 3, 4, 5), "SCALAR", 6, 5123),
    ]
    attributes = {"POSITION": 0, "NORMAL": 1, "TEXCOORD_0": 2}
    if authored_tangents:
        attributes["TANGENT"] = len(streams)
        # Vary both basis vectors so Assimp's invalid-data pass keeps the authored arrays.
        tangents = (0, 1, 0, 1) * 3 + (0, -1, 0, 1) * 3
        streams.append((struct.pack("<24f", *tangents), "VEC4", 6, 5126))
    binary = bytearray()
    views, accessors = [], []
    for data, kind, count, component in streams:
        views.append({"buffer": 0, "byteOffset": len(binary), "byteLength": len(data)})
        accessors.append({"bufferView": len(views) - 1, "componentType": component, "count": count, "type": kind})
        binary.extend(data)
    accessors[0].update(min=[0, 0, 0], max=[1, 1, 0])
    document = {
        "asset": {"version": "2.0"},
        "buffers": [{"byteLength": len(binary)}],
        "bufferViews": views,
        "accessors": accessors,
        "materials": [{}],
        "meshes": [{"primitives": [{"attributes": attributes, "indices": 3, "material": 0}]}],
        "nodes": [{"mesh": 0}],
        "scenes": [{"nodes": [0]}],
        "scene": 0,
    }
    encoded = json.dumps(document, separators=(",", ":")).encode()
    encoded += b" " * (-len(encoded) % 4)
    model_path = tmp_path / "mirrored-uv-seam.glb"
    model_path.write_bytes(
        struct.pack("<4sII", b"glTF", 2, 28 + len(encoded) + len(binary))
        + struct.pack("<I4s", len(encoded), b"JSON")
        + encoded
        + struct.pack("<I4s", len(binary), b"BIN\0")
        + binary
    )
    mocker.patch.object(renpy.loader, "loadable", return_value=True)

    def open_model(filename):
        """Transfer the file to Assimp's SDL stream, which closes it after import."""
        return Path(filename).open("rb")

    mocker.patch.object(renpy.loader, "load", side_effect=open_model)
    mocker.patch.object(renpy.display.im.cache, "preload_image")
    plain = assimp.GLTFModel(model_path.as_posix(), shader="renpy.texture", material=standard).load()
    tangent = assimp.GLTFModel(model_path.as_posix(), shader="renpy.texture", tangents=True, material=standard).load()
    assert tangent.memory_size > plain.memory_size > 0
    assert len(plain.mesh_info) == len(tangent.mesh_info) == 1
    first, second = plain.mesh_info[0].mesh, tangent.mesh_info[0].mesh
    assert first.get_points() == second.get_points()
    assert first.get_triangles() == second.get_triangles()
    assert first.points == second.points == 6
    assert first.triangles == second.triangles == 2
    assert "a_tangent" not in first.layout.offset
    assert "a_bitangent" not in first.layout.offset
    assert "a_tangent" in second.layout.offset
    assert "a_bitangent" in second.layout.offset
    plain_rows, tangent_rows = _native_attribute_rows(first), _native_attribute_rows(second)
    for name, length in (("a_tex_coord", 2), ("a_normal", 3)):
        first_offset, second_offset = first.layout.offset[name], second.layout.offset[name]
        assert [row[first_offset : first_offset + length] for row in plain_rows] == [
            row[second_offset : second_offset + length] for row in tangent_rows
        ]
    normal_offset = second.layout.offset["a_normal"]
    tangent_offset = second.layout.offset["a_tangent"]
    uv_offset = first.layout.offset["a_tex_coord"]
    for row in tangent_rows:
        for name in ("a_normal", "a_tangent", "a_bitangent"):
            offset = second.layout.offset[name]
            vector = row[offset : offset + 3]
            assert len(vector) == 3
            assert all(math.isfinite(value) for value in vector)
            assert sum(value * value for value in vector) == pytest.approx(1.0)
        assert row[normal_offset : normal_offset + 3] == (0, 0, 1)
    for position in ((0, 0, 0), (1, 1, 0)):
        assert {row[uv_offset] for point, row in zip(first.get_points(), plain_rows) if point[:3] == position} == {0, 1}
    if authored_tangents:
        assert {row[tangent_offset : tangent_offset + 3] for row in tangent_rows} == {(0, 1, 0), (0, -1, 0)}
        return
    assert {row[tangent_offset : tangent_offset + 3] for row in tangent_rows} == {(1, 0, 0), (-1, 0, 0)}
    for point, row in zip(second.get_points(), tangent_rows):
        if point[:3] == (0, 0, 0):
            expected_tangent = (1, 0, 0) if row[second.layout.offset["a_tex_coord"]] == 0 else (-1, 0, 0)
            assert row[tangent_offset : tangent_offset + 3] == expected_tangent


@pytest.mark.parametrize("operation", ["load", "visit"])
def test_cached_model_does_not_wait_for_an_unrelated_import(model_cache, operation):
    """Rendering or visiting a resident room must not wait behind an unrelated import."""
    model = assimp.GLTFModel("resident.glb", shader="renpy.texture")
    data = assimp.ModelData()
    data.memory_size, data.mesh_info = 1, []
    model_cache[model.cache_key] = data
    with ThreadPoolExecutor(max_workers=1) as pool, assimp.loader_lock:
        result = pool.submit(getattr(model, operation)).result(timeout=1)
        if operation == "load":
            assert result is data
        else:
            assert result == []


# Material alpha and saved-model compatibility.

@pytest.fixture
def material_model(model_cache, mocker):
    """Load shipped glTF fixtures through the real importer without starting image prediction threads."""
    mocker.patch.object(renpy.loader, "loadable", side_effect=lambda filename: Path(filename).is_file())

    def open_model(filename):
        """Assimp owns and closes the returned file through its SDL stream."""
        return Path(filename).open("rb")

    mocker.patch.object(renpy.loader, "load", side_effect=open_model)
    mocker.patch.object(renpy.display.im.cache, "preload_image")

    def load(mode, **kwargs):
        filename = Path(__file__).parents[1] / "game" / "models" / f"alpha-{mode}.gltf"
        return assimp.GLTFModel(filename.as_posix(), shader="renpy.texture", **kwargs)

    return load


@pytest.mark.parametrize(("mode", "expected"), [("opaque", 0.0), ("mask", 1.0), ("blend", 2.0)])
def test_import_exposes_material_alpha_modes(material_model, mode, expected):
    """Shaders receive the native material mode, cutoff, base alpha, and double-sided setting."""
    data = material_model(mode, material_alpha=True).load()
    assert len(data.mesh_info) == 1
    material = data.mesh_info[0]
    assert material.uniforms["u_material_gltf_alphamode"] == expected
    assert material.uniforms["u_material_gltf_alphacutoff"] == pytest.approx(0.3)
    assert material.uniforms["u_color_diffuse"][3] == (0.0 if mode == "opaque" else 0.5)
    assert material.twosided


def test_material_alpha_does_not_share_incompatible_textures(material_model):
    """Straight-alpha and legacy premultiplied imports coexist, while equal options still share an import."""
    legacy = material_model("blend")
    straight = material_model("blend", material_alpha=True)
    assert legacy.load() is not straight.load()
    assert straight.load() is material_model("blend", material_alpha=True).load()


def test_import_defaults_missing_alpha_settings(material_model, tmp_path):
    """Materials without alpha settings use glTF's opaque mode and 0.5 cutoff defaults."""
    document = json.loads(Path(material_model("opaque").filename).read_text(encoding="utf-8"))
    material = document["materials"][0]
    del material["alphaMode"]
    del material["alphaCutoff"]
    filename = tmp_path / "default-alpha.gltf"
    filename.write_text(json.dumps(document), encoding="utf-8")
    data = assimp.GLTFModel(filename.as_posix(), shader="renpy.texture", material_alpha=True).load()
    assert data.mesh_info[0].uniforms["u_material_gltf_alphamode"] == 0.0
    assert data.mesh_info[0].uniforms["u_material_gltf_alphacutoff"] == 0.5


def test_native_texture_uri_is_percent_decoded(material_model, tmp_path):
    """Assimp reports image URIs undecoded, so the texture must load from the decoded file name."""
    document = json.loads(Path(material_model("mask").filename).read_text(encoding="utf-8"))
    (tmp_path / "base color.png").write_bytes(base64.b64decode(document["images"][0]["uri"].partition(",")[2]))
    document["images"][0]["uri"] = "base%20color.png"
    filename = tmp_path / "encoded-image.gltf"
    filename.write_text(json.dumps(document), encoding="utf-8")
    data = assimp.GLTFModel(filename.as_posix(), shader="renpy.texture", material_alpha=True).load()
    texture = data.mesh_info[0].texture_uniforms["u_tex_base_color"]
    assert texture.image.filename == (tmp_path / "base color.png").as_posix()


def test_old_saved_model_preserves_legacy_materials():
    """An old save has no material_alpha attribute and must retain its original texture and drawing behavior."""
    model = assimp.GLTFModel("old.glb", shader="renpy.texture")
    del model.material_alpha
    del model.material
    restored = pickle.loads(pickle.dumps(model))
    assert restored.material_alpha is False
    assert restored.material is False
    assert restored.cache_key == model.cache_key


# Standard material metadata and validation.

@pytest.fixture
def standard_model(material_model, tmp_path):
    """Reuse the real filesystem/import harness with a self-contained material conformance scene."""
    path = Path(__file__).parents[1] / "game" / "models" / "material-grid.gltf"

    def create(edit=None, glb=False, **kwargs):
        document = json.loads(path.read_text(encoding="utf-8"))
        if edit is not None:
            edit(document)
        filename = tmp_path / ("material.glb" if glb else "material.gltf")
        data = json.dumps(document).encode("utf-8")
        if glb:
            binary = base64.b64decode(document["buffers"][0].pop("uri").partition(",")[2])
            binary += b"\0" * (-len(binary) % 4)
            data = json.dumps(document).encode("utf-8")
            data += b" " * (-len(data) % 4)
            data = (struct.pack("<4sII", b"glTF", 2, len(data) + len(binary) + 28)
                + struct.pack("<I4s", len(data), b"JSON") + data
                + struct.pack("<I4s", len(binary), b"BIN\0") + binary)
        filename.write_bytes(data)
        return assimp.GLTFModel(filename.as_posix(), material=True, **kwargs)

    return create


@pytest.mark.parametrize("glb", [False, True], ids=["gltf", "glb"])
def test_standard_materials_keep_authored_indices_and_defaults(standard_model, glb):
    """Repeated materials keep their own metadata; default factors are always available to custom shaders."""
    data = standard_model(glb=glb).load()
    assert len(data.mesh_info) == 12
    uniforms = data.mesh_info[0].uniforms
    assert uniforms["u_color_base"] == (1, 1, 1, 1)
    assert uniforms["u_material_metallicfactor"] == 1.0
    assert uniforms["u_material_roughnessfactor"] == 1.0
    assert uniforms["u_material_emissivefactor"] == (0, 0, 0)
    assert uniforms["u_material_normal_scale"] == 1.0
    assert uniforms["u_material_occlusion_strength"] == 1.0
    assert data.mesh_info[1].uniforms["u_material_base_color_texcoord"] == 1.0
    assert data.mesh_info[3].uniforms["u_material_base_color_texcoord"] == 1.0
    assert data.mesh_info[7].uniforms["u_material_base_color_uv0"] == pytest.approx((0, -1, 0.9))
    assert data.mesh_info[7].uniforms["u_material_base_color_uv1"] == pytest.approx((2, 0, 0.3))


def test_standard_material_defaults_without_authored_materials(standard_model):
    """Assimp's appended default material follows glTF's white, opaque defaults even without a material table."""
    def edit(document):
        del document["materials"]
        for mesh in document["meshes"]:
            del mesh["primitives"][0]["material"]

    data = standard_model(edit).load()
    assert data.mesh_info
    assert all(mi.uniforms["u_color_base"] == (1, 1, 1, 1) for mi in data.mesh_info)


@pytest.mark.parametrize("names", ["unique", "missing", "duplicate", "long", "long_utf8"])
@pytest.mark.parametrize("glb", [False, True], ids=["gltf", "glb"])
def test_standard_materials_survive_assimp_renumbering(standard_model, model_cache, glb, names):
    """Assimp numbers only referenced materials, by first use; each mesh must still bind its own document material."""
    expected = [(mi.uniforms, set(mi.texture_uniforms)) for mi in standard_model(glb=glb).load().mesh_info]
    model_cache.clear()

    def edit(document):
        count = len(document["materials"])
        document["materials"] = [{"name": "Unused"}] + document["materials"][::-1]
        for mesh in document["meshes"]:
            mesh["primitives"][0]["material"] = count - mesh["primitives"][0]["material"]
        for i, material in enumerate(document["materials"]):
            if names == "missing":
                del material["name"]
            elif names == "duplicate":
                material["name"] = "Paint"
            elif names == "long":
                material["name"] = f"{i:02d}" + "x" * 1050
            elif names == "long_utf8":
                material["name"] = f"{i:02d}" + "é" * 600

    data = standard_model(edit, glb=glb).load()
    assert [(mi.uniforms, set(mi.texture_uniforms)) for mi in data.mesh_info] == expected
    assert [mi.material_index for mi in data.mesh_info] == list(range(12, 0, -1))


def test_extension_images_in_one_buffer_share_a_reader(standard_model, mocker):
    """Each embedded extension map used to construct its own reader and reread the shared binary buffer."""
    def edit(document):
        png = base64.b64decode(document["images"][0]["uri"].partition(",")[2])
        buffer = document["buffers"][0]
        data = base64.b64decode(buffer["uri"].partition(",")[2])
        data += b"\0" * (-len(data) % 4)
        for _ in range(2):
            document["bufferViews"].append({"buffer": 0, "byteOffset": len(data), "byteLength": len(png)})
            data += png + b"\0" * (-len(png) % 4)
            document["images"].append({"bufferView": len(document["bufferViews"]) - 1, "mimeType": "image/png"})
            document["textures"].append({"source": len(document["images"]) - 1})
        buffer["uri"] = "data:application/octet-stream;base64," + base64.b64encode(data).decode()
        buffer["byteLength"] = len(data)
        count = len(document["textures"])
        document["extensionsUsed"].append("KHR_materials_specular")
        document["materials"][0]["extensions"] = {"KHR_materials_specular": {
            "specularTexture": {"index": count - 2}, "specularColorTexture": {"index": count - 1}}}

    readers = mocker.spy(renpy.gl2.gltfscene, "Accessors")
    data = standard_model(edit, glb=True).load()
    textures = data.mesh_info[0].texture_uniforms
    assert all(isinstance(textures[name].image, renpy.display.im.UnoptimizedTexture)
        for name in ("u_tex_specular", "u_tex_specular_color"))
    assert readers.call_count == 1


def test_layered_material_factors_and_independent_texture_bindings(standard_model):
    """Extension factors retain their ranges and channels, and specular-color maps decode sRGB independently of weights."""
    def edit(document):
        document["materials"][0]["extensions"] = {
            "KHR_materials_specular": {"specularFactor": 0.25, "specularColorFactor": [2, 0.5, 1],
                "specularTexture": {"index": 0}, "specularColorTexture": {"index": 0, "texCoord": 1}},
            "KHR_materials_clearcoat": {"clearcoatFactor": 0.8, "clearcoatRoughnessFactor": 0.3,
                "clearcoatNormalTexture": {"index": 0, "scale": 0.4}},
            "KHR_materials_sheen": {"sheenColorFactor": [0.1, 0.2, 0.4], "sheenRoughnessFactor": 0.6},
        }

    data = standard_model(edit).load()
    material = data.mesh_info[0]
    assert material.uniforms["u_material_specular_factor"] == 0.25
    assert material.uniforms["u_material_specular_color_factor"] == (2, 0.5, 1)
    assert material.uniforms["u_material_clearcoat_factor"] == 0.8
    assert material.uniforms["u_material_clearcoat_normal_scale"] == 0.4
    assert material.uniforms["u_material_sheen_color_factor"] == (0.1, 0.2, 0.4)
    assert material.uniforms["u_material_specular_color_texcoord"] == 1
    assert not material.texture_uniforms["u_tex_specular"].srgb
    assert material.texture_uniforms["u_tex_specular_color"].srgb


@pytest.mark.parametrize("extension,field,value", [
    ("KHR_materials_specular", "specularFactor", -0.1),
    ("KHR_materials_specular", "specularFactor", None),
    ("KHR_materials_specular", "specularColorFactor", None),
    ("KHR_materials_specular", "specularColorFactor", [-1, 0, 0]),
    ("KHR_materials_clearcoat", "clearcoatFactor", 2),
    ("KHR_materials_sheen", "sheenRoughnessFactor", -1),
    ("KHR_materials_sheen", "sheenColorFactor", [1, 2, 1]),
])
def test_layered_material_rejects_invalid_factors(standard_model, extension, field, value):
    """Invalid source factors fail during import rather than reaching shader math or sampler setup."""
    def edit(document):
        document["materials"][0]["extensions"] = {extension: {field: value}}

    with pytest.raises(ValueError):
        standard_model(edit).load()


def test_standard_texture_prediction_keeps_only_bound_overrides(standard_model):
    """Prediction must request sampler-aware bindings once, without the replaced parent's raw-color copy."""
    replacement = renpy.display.im.Null("#135")
    model = standard_model(u_tex_base_color=replacement)
    parent = model.texture_uniforms["u_tex_base_color"]
    model.load()
    textures = model.visit()
    assert len(textures) == len(set(textures))
    overridden = [texture for texture in textures if texture.image == parent]
    assert overridden and all(texture.srgb for texture in overridden)
    model.texture_uniforms["u_tex_base_color"] = renpy.display.im.Null("#abc")
    changed = model.visit()
    assert not any(texture.image == parent for texture in changed)
    assert any(texture.image == model.texture_uniforms["u_tex_base_color"] for texture in changed)


@pytest.mark.parametrize("attribute", ["POSITION", "TEXCOORD_0"])
def test_standard_material_rejects_nonfinite_geometry(standard_model, attribute):
    """Keeping valid constant attributes must not admit NaN geometry that the old cleanup pass removed."""
    def edit(document):
        index = document["meshes"][0]["primitives"][0]["attributes"][attribute]
        view = document["bufferViews"][document["accessors"][index]["bufferView"]]
        binary = bytearray(base64.b64decode(document["buffers"][0]["uri"].partition(",")[2]))
        struct.pack_into("<f", binary, view["byteOffset"], float("nan"))
        document["buffers"][0]["uri"] = "data:application/octet-stream;base64," + base64.b64encode(binary).decode()

    with pytest.raises(ValueError, match="finite"):
        standard_model(edit).load()


@pytest.mark.parametrize(("field", "value"), [("wrapS", 0), ("magFilter", 9987), ("minFilter", 0)])
def test_standard_material_rejects_invalid_samplers(standard_model, field, value):
    """Invalid sampler constants fail during loading rather than producing incomplete GPU textures."""
    def edit(document):
        document["samplers"][0][field] = value

    with pytest.raises(ValueError, match="wrap|filters"):
        standard_model(edit).load()


@pytest.mark.parametrize("coord", [2, 3])
def test_standard_material_rejects_unsupported_uv_sets(standard_model, coord):
    """The supported UV range is explicit; silently selecting the wrong coordinates would corrupt the material."""
    def edit(document):
        document["materials"][0]["pbrMetallicRoughness"]["baseColorTexture"]["texCoord"] = coord

    with pytest.raises(ValueError, match="UV set"):
        standard_model(edit).load()


def test_standard_material_rejects_missing_uvs(standard_model):
    """A referenced texture coordinate set must actually exist on every mesh using that material."""
    def edit(document):
        document["meshes"][1]["primitives"][0]["attributes"].pop("TEXCOORD_1")

    with pytest.raises(ValueError, match="missing UV set 1"):
        standard_model(edit).load()


@pytest.mark.parametrize("data", [b"glTF", struct.pack("<4sII", b"glTF", 2, 40) + struct.pack("<I4s", 20, b"JSON")])
def test_material_document_rejects_truncated_glb(mocker, data):
    """GLB metadata lengths are checked before JSON parsing, without reading the geometry chunk."""
    import io

    mocker.patch.object(renpy.loader, "load", side_effect=lambda filename: io.BytesIO(data))
    with pytest.raises(ValueError, match="Truncated"):
        assimp._material_document("truncated.glb")


# PBR scene setup and saved scene graphs.

def test_pbr_uses_standard_materials_and_tangents(standard_model):
    """Selecting PBR supplies the standard texture contract and generated tangent basis without extra import flags."""
    model = standard_model(shader="renpy.gltf.pbr")
    assert model.material and model.material_alpha and model.tangents
    data = model.load()
    assert all("a_tangent" in mi.mesh.layout.offset for mi in data.mesh_info)


def test_standard_material_preserves_unlit_and_two_sided(standard_model):
    """The canonical source material controls the lighting bypass and back-face normal convention."""
    def edit(document):
        document["materials"][0].setdefault("extensions", {})["KHR_materials_unlit"] = {}
        document["materials"][0]["doubleSided"] = True
        document["materials"][1]["doubleSided"] = False

    data = standard_model(edit).load()
    assert data.mesh_info[0].uniforms["u_material_unlit"] == 1.0
    assert data.mesh_info[0].uniforms["u_material_twosided"] == 1.0
    assert data.mesh_info[1].uniforms["u_material_unlit"] == 0.0
    assert data.mesh_info[1].uniforms["u_material_twosided"] == 0.0


@pytest.mark.parametrize("strength", [0, 1, 8])
def test_standard_material_imports_emissive_strength(standard_model, strength):
    """An authored emissive strength stays separate from the color factor, allowing independent overrides."""
    def edit(document):
        document["materials"][0]["emissiveFactor"] = [0.5, 0.25, 1]
        document["materials"][0].setdefault("extensions", {})["KHR_materials_emissive_strength"] = {
            "emissiveStrength": strength,
        }

    data = standard_model(edit).load()
    assert data.mesh_info[0].uniforms["u_material_emissivefactor"] == (0.5, 0.25, 1)
    assert data.mesh_info[0].uniforms["u_material_emissive_strength"] == strength
    assert data.mesh_info[1].uniforms["u_material_emissive_strength"] == 1


@pytest.mark.parametrize("strength", [-1, math.inf, math.nan, "8", None])
def test_standard_material_rejects_invalid_emissive_strength(standard_model, strength):
    """Negative, non-finite, and non-numeric multipliers cannot reach the lighting shader."""
    def edit(document):
        document["materials"][0].setdefault("extensions", {})["KHR_materials_emissive_strength"] = {
            "emissiveStrength": strength,
        }

    with pytest.raises(Exception, match="emissiveStrength|loading"):
        standard_model(edit).load()


def test_standard_material_rejects_emissive_strength_on_unlit(standard_model):
    """The glTF extension forbids combining an emissive multiplier with the unlit extension."""
    def edit(document):
        document["materials"][0]["extensions"] = {
            "KHR_materials_emissive_strength": {}, "KHR_materials_unlit": {},
        }

    with pytest.raises(ValueError, match="cannot be combined"):
        standard_model(edit).load()


@pytest.mark.parametrize("ior", [0, 1, 1.5, 2.4])
def test_standard_material_imports_optical_properties(standard_model, ior):
    """Optical factors are canonical source values, including the extension's special zero-IOR mode."""
    def edit(document):
        document["materials"][0]["extensions"] = {
            "KHR_materials_transmission": {"transmissionFactor": 0.75},
            "KHR_materials_volume": {"thicknessFactor": 0.2, "attenuationDistance": 3,
                "attenuationColor": [0.25, 0.5, 1]},
            "KHR_materials_ior": {"ior": ior},
        }

    data = standard_model(edit).load()
    values = data.mesh_info[0].uniforms
    assert values["u_material_transmission_factor"] == 0.75
    assert values["u_material_thickness_factor"] == 0.2
    assert values["u_material_attenuation_distance"] == 3
    assert values["u_material_attenuation_color"] == (0.25, 0.5, 1)
    assert values["u_material_ior"] == ior
    defaults = data.mesh_info[1].uniforms
    assert defaults["u_material_transmission_factor"] == defaults["u_material_thickness_factor"] == 0
    assert defaults["u_material_attenuation_distance"] == 0  # Infinite attenuation distance.
    assert defaults["u_material_attenuation_color"] == (1, 1, 1)
    assert defaults["u_material_ior"] == 1.5


@pytest.mark.parametrize(("extension", "field", "value"), [
    ("transmission", "transmissionFactor", -1), ("transmission", "transmissionFactor", 1.1),
    ("transmission", "transmissionFactor", math.nan), ("volume", "thicknessFactor", -1),
    ("volume", "attenuationDistance", 0), ("volume", "attenuationDistance", math.inf),
    ("volume", "attenuationColor", [1, 0, -1]), ("volume", "attenuationColor", [1, 2, 1]),
    ("ior", "ior", -1), ("ior", "ior", 0.5), ("ior", "ior", "1.5"),
])
def test_standard_material_rejects_invalid_optical_properties(standard_model, extension, field, value):
    """Invalid source optics fail at import rather than producing NaNs or undefined texture samples on the GPU."""
    def edit(document):
        document["materials"][0]["extensions"] = {"KHR_materials_" + extension: {field: value}}

    with pytest.raises(Exception, match=field + "|loading"):
        standard_model(edit).load()


@pytest.mark.parametrize("extension", ["transmission", "volume", "ior"])
def test_standard_material_rejects_optical_properties_on_unlit(standard_model, extension):
    """The glTF optical extensions explicitly exclude unlit materials."""
    def edit(document):
        document["materials"][0]["extensions"] = {"KHR_materials_" + extension: {}, "KHR_materials_unlit": {}}

    with pytest.raises(ValueError, match="cannot be combined"):
        standard_model(edit).load()


def test_import_accepts_light_only_assets(standard_model):
    """A light rig needs no dummy geometry to get through Assimp's mesh-only import boundary."""
    def edit(document):
        document.pop("meshes")
        document["extensions"] = {"KHR_lights_punctual": {"lights": [{"type": "point", "intensity": 4}]}}
        document["nodes"] = [{"extensions": {"KHR_lights_punctual": {"light": 0}}}]
        document["scenes"] = [{"nodes": [0]}]
        document["scene"] = 0

    data = standard_model(edit).load()
    assert data.mesh_info == []
    assert len(data.lights) == 1 and data.lights[0][0].intensity == 4


def test_light_overrides_require_standard_import():
    """Legacy imports have no light graph, so overrides must not silently do nothing."""
    with pytest.raises(ValueError, match="standard material"):
        assimp.GLTFModel("room.glb", shader="renpy.texture", light_overrides={"Lamp": {"intensity": 0}})


def test_unknown_light_override_fails_even_without_imported_lights(standard_model, mocker):
    """A misspelled light name is an error even when the model contains no light nodes."""
    model = standard_model(light_overrides={"missing": {"intensity": 0}})
    mocker.patch.object(renpy.display.im.cache, "get_textures")
    with pytest.raises(ValueError, match="Unknown imported light"):
        model.render(400, 300, 0, 0)


@pytest.mark.parametrize(
    "options",
    [
        {"light_direction": (0, 0, 0)},
        {"light_direction": (0, math.inf, 1)},
        {"light_color": (1, -1, 1)},
        {"environment_intensity": -1},
        {"environment_rotation": math.nan},
        {"exposure": -1},
        {"exposure": "1"},
        {"environment": renpy.display.layout.Null()},
        {"environment_quality": "ultra"},
        {"meters_per_unit": 0},
        {"shadows": 1},
        {"shadow_size": 63},
        {"shadow_size": 2049},
        {"shadow_bias": -0.001},
        {"transmission": 1},
        {"transparency_layers": True},
        {"transparency_layers": -1},
        {"transparency_layers": 9},
        {"refraction_margin": -0.1},
        {"refraction_margin": 0.6},
        {"refraction_margin": math.nan},
        {"shadow_cascades": True},
        {"shadow_cascades": 3},
        {"shadow_distance": 0},
        {"shadow_distance": -1},
        {"shadow_distance": math.inf},
        {"colored_shadows": 1},
        {"caustics": 1},
        {"lights": [object()]},
        {"import_lights": 1},
        {"tone_mapping": "aces"},
    ],
)
def test_pbr_scene_rejects_invalid_lighting(options):
    """Invalid lighting fails before rendering rather than feeding non-finite values or unsupported textures to GLSL."""
    with pytest.raises(ValueError):
        assimp.GLTFScene(renpy.display.layout.Null(), **options)


def test_pbr_scene_pickles_environment_and_lighting():
    """A saved scene retains its lighting and image graph without storing GPU resources or cached renders."""
    scene = assimp.GLTFScene(renpy.display.layout.Null(), light_direction=(0, -3, 4),
        light_color=(4, 2, 1), environment=renpy.display.im.Null("#808080"),
        environment_rotation=90, environment_intensity=2, exposure=0.5)
    restored = pickle.loads(pickle.dumps(scene))
    assert restored.light_direction == pytest.approx((0, -0.6, 0.8))
    assert restored.light_color == (4, 2, 1)
    assert restored.environment_intensity == 2
    assert restored.environment_rotation == 90
    assert restored.exposure == 0.5
    assert restored.visit()[0] is restored.child
    assert restored.environment.textures[0].displayable.srgb


# Named scene instances and node-transform animation.

@pytest.mark.parametrize("options", [
    {"box_max": (0, 0, 0)}, {"position": (2, 0, 0)}, {"position": (-1, 0, 0)},
    {"fade": -1}, {"intensity": math.nan}, {"rotation": math.inf},
])
def test_reflection_probe_rejects_invalid_box_and_lighting(options):
    """A finite capture must be inside a nonempty box; invalid values never reach GPU ray-box division."""
    settings = {"position": (0, 0, 0), "box_min": (-1, -1, -1), "box_max": (1, 1, 1)}
    settings.update(options)
    with pytest.raises(ValueError):
        assimp.GLTFReflectionProbe(renpy.display.im.Null("#fff"), **settings)


def test_reflection_probe_scene_pickles_only_saved_image_graph():
    """Probes and their filtering quality survive saves without serializing native textures or render-cache entries."""
    probe = assimp.GLTFReflectionProbe("room.hdr", position=(0, 0, 0), box_min=(-1, -2, -3),
        box_max=(1, 2, 3), rotation=90, fade=0.25, intensity=2)
    scene = assimp.GLTFScene(renpy.display.layout.Null(), reflection_probes=(probe,), environment_quality="high")
    restored = pickle.loads(pickle.dumps(scene))
    assert restored.reflection_probes[0].box_min == (-1, -2, -3)
    assert restored.reflection_probes[0].rotation == 90
    assert restored.environment_quality == "high"
    assert not restored.reflection_models[0].textures[0].displayable.srgb
    with pytest.raises(ValueError, match="at most two"):
        assimp.GLTFScene(renpy.display.layout.Null(), reflection_probes=(probe,) * 3)


def test_old_environment_scene_shape_uses_no_local_probes():
    """Saved scenes from before local probes keep their original lighting with compatibility defaults."""
    scene = assimp.GLTFScene(renpy.display.layout.Null(), environment="room.png")
    for name in ("reflection_probes", "reflection_models", "environment_quality", "environment_fallback"):
        delattr(scene, name)
    restored = pickle.loads(pickle.dumps(scene))
    assert restored.reflection_probes == () and restored.reflection_models == ()
    assert restored.environment_quality == "low"

@pytest.fixture
def scene_model(material_model, tmp_path):
    """Reuse the real native import harness with a hierarchy and animation stored in three buffer transports."""
    path = Path(__file__).parents[1] / "game/models/scene-controls.gltf"

    def create(edit=None, transport="embedded", **kwargs):
        document = json.loads(path.read_text(encoding="utf-8"))
        if edit is not None:
            edit(document)
        filename = tmp_path / ("scene.glb" if transport == "glb" else "scene.gltf")
        binary = base64.b64decode(document["buffers"][0]["uri"].partition(",")[2])
        if transport == "external":
            document["buffers"][0]["uri"] = "scene.bin"
            (tmp_path / "scene.bin").write_bytes(binary)
        elif transport == "glb":
            document["buffers"][0].pop("uri")
        encoded = json.dumps(document).encode()
        if transport == "glb":
            encoded += b" " * (-len(encoded) % 4)
            binary += b"\0" * (-len(binary) % 4)
            encoded = (struct.pack("<4sII", b"glTF", 2, len(encoded) + len(binary) + 28)
                + struct.pack("<I4s", len(encoded), b"JSON") + encoded
                + struct.pack("<I4s", len(binary), b"BIN\0") + binary)
        filename.write_bytes(encoded)
        kwargs.setdefault("scene", True)
        return assimp.GLTFModel(filename.as_posix(), material=True, **kwargs)

    return create


@pytest.mark.parametrize("transport", ["embedded", "external", "glb"])
def test_native_scene_import_keeps_named_graph_and_animation(scene_model, transport):
    """Real geometry and animation metadata load together from glTF data URIs, external buffers, and GLB BIN chunks."""
    model = scene_model(transport=transport)
    info = model.scene_info()
    assert info["nodes"][1]["name"] == "Door" and info["nodes"][3]["parent"] == 1
    assert info["materials"][0]["name"] == "DoorPaint"
    assert info["animations"][0]["duration"] == 2
    assert len(model.load().mesh_info) == 2


def test_native_import_decodes_percent_encoded_buffer_uri(scene_model, tmp_path):
    """Assimp requests external buffers by their undecoded URI; the animation reader already decodes it."""
    model = scene_model(transport="external")
    document = tmp_path / "scene.gltf"
    document.write_text(document.read_text(encoding="utf-8").replace('"scene.bin"', '"scene%20data.bin"'), encoding="utf-8")
    (tmp_path / "scene.bin").rename(tmp_path / "scene data.bin")
    assert len(model.load().mesh_info) == 2
    assert model.scene_info()["animations"][0]["duration"] == 2


def test_scene_instances_share_import_without_sharing_controls(scene_model):
    """A different pose or paint choice reuses geometry; static imports retain a separate compatible cache entry."""
    first = scene_model(animation="Slide", animation_time=0)
    second = scene_model(node_overrides={"Door": {"visible": False}},
        material_overrides={"WallPaint": {"u_color_base": (0, 1, 0, 1)}})
    assert first.load() is second.load()
    assert first.load() is not scene_model(scene=False).load()
    assert first.node_overrides == () and first.material_overrides == ()


def test_scene_material_overrides_use_document_indices(scene_model):
    """The Door mesh is reached first, so Assimp numbers WallPaint 0; overrides still resolve to WallPaint's mesh."""
    def edit(document):
        document["meshes"][0]["primitives"][0]["material"] = 1
        document["meshes"][1]["primitives"][0]["material"] = 0

    model = scene_model(edit, material_overrides={"WallPaint": {"u_color_base": (0, 1, 0, 1)}})
    data = model.load()
    door = next(blit.mesh_info for blit in data.blit_info if blit.node_index == 1)
    assert door.material_index == 1
    assert set(model._materials(data)) == {door.material_index}


def test_material_override_binds_custom_sampler(scene_model):
    """A sampler the source material lacks must still reach the meshes using the overridden material."""
    model = scene_model(material_overrides={"DoorPaint": {"u_tex_custom": "custom.png"}})
    data = model.load()
    door = next(blit.mesh_info for blit in data.blit_info if blit.node_index == 1)
    _materials, mesh_textures, root_textures, textures = model._prepared(data)
    assert mesh_textures[door]["u_tex_custom"].image.filename == "custom.png"
    assert all("u_tex_custom" not in values for mi, values in mesh_textures.items() if mi is not door)
    assert "u_tex_custom" not in root_textures and mesh_textures[door]["u_tex_custom"] in textures


def test_scene_import_keeps_multiple_roots_and_multi_primitive_nodes(scene_model):
    """Source-node identities survive Assimp's synthetic root and the splitting of a source mesh into primitives."""
    def edit(document):
        document["scenes"][0]["nodes"] = [1, 2]
        document["meshes"][0]["primitives"].append(dict(document["meshes"][1]["primitives"][0]))

    model = scene_model(edit)
    data = model.load()
    assert len(data.blit_info) == 3
    assert model.scene_info()["nodes"][0]["active"] is False
    assert model.scene_info()["nodes"][3]["parent"] == 1
    matrices, _visible = data.asset.pose((), "Slide", 1, False, 100)
    assert matrices[1].transform(0, 0, 0, components=3) == pytest.approx((0, 0, 20), abs=1e-5)


def test_scene_options_survive_pickling_without_native_data(scene_model):
    """Saves retain immutable instance settings and recreate shared meshes and animation metadata on demand."""
    model = scene_model(animation="Slide", animation_time=1, animation_speed=-1, animation_offset=2,
        node_overrides={"Lamp": {"visible": False}},
        material_overrides={"DoorPaint": {"u_tex_base_color": renpy.display.im.Null("#0f0")}})
    model.load()
    restored = pickle.loads(pickle.dumps(model))
    assert restored.animation == "Slide" and restored.animation_time == 1
    assert restored.animation_speed == -1 and restored.animation_offset == 2
    assert restored.node_overrides == model.node_overrides
    assert restored.scene_info()["animations"][0]["name"] == "Slide"


def test_old_model_shape_preserves_static_import():
    """Class defaults let pre-scene saves load with the original cache key and no automatic animation."""
    model = assimp.GLTFModel("old.glb", shader="renpy.texture")
    for name in ("scene", "node_overrides", "material_overrides", "animation", "animation_time",
            "animation_loop", "animation_speed", "animation_offset"):
        delattr(model, name)
    restored = pickle.loads(pickle.dumps(model))
    assert not restored.scene and restored.animation is None
    assert restored.node_overrides == restored.material_overrides == ()
    assert restored.cache_key == ("old.glb", False, 1.0, False, False)


@pytest.fixture
def deform_model(material_model, tmp_path):
    """Import authored skin/morph data with duplicate joint names through the native material/file boundary."""
    source = Path(__file__).parents[1] / "game/models/scene-deformation.gltf"

    def create(edit=None, transport="embedded", **settings):
        filename = tmp_path / ("deformation.glb" if transport == "glb" else "deformation.gltf")
        if edit is not None or not filename.exists():
            document = json.loads(source.read_text(encoding="utf-8"))
            if edit is not None:
                edit(document)
            binary = base64.b64decode(document["buffers"][0]["uri"].partition(",")[2])
            if transport == "external":
                document["buffers"][0]["uri"] = "deformation.bin"
                (tmp_path / "deformation.bin").write_bytes(binary)
            elif transport == "glb":
                document["buffers"][0].pop("uri")
            encoded = json.dumps(document).encode("utf-8")
            if transport == "glb":
                encoded += b" " * (-len(encoded) % 4)
                binary += b"\0" * (-len(binary) % 4)
                encoded = (struct.pack("<4sII", b"glTF", 2, len(encoded) + len(binary) + 28)
                    + struct.pack("<I4s", len(encoded), b"JSON") + encoded
                    + struct.pack("<I4s", len(binary), b"BIN\0") + binary)
            filename.write_bytes(encoded)
        return assimp.GLTFModel(filename.as_posix(), scene=True, zoom=100, **settings)

    return create


@pytest.mark.parametrize("transport", ["embedded", "external", "glb"])
def test_deformation_keeps_source_vertices_and_duplicate_joint_identities(deform_model, transport):
    """Native postprocessing cannot deduplicate different morph identities or conflate joints with the same name."""
    model = deform_model(transport=transport)
    data = model.load()
    assert len(data.mesh_info[0].mesh.get_points()) == 4
    assert model.scene_info()["skins"][0]["joints"] == (3, 4)
    assert model.scene_info()["warnings"] == ()
    assert data.scene_memory_size > len(data.mesh_info[0].deformation.targets) * 4


def test_morph_precedes_skin_and_preserves_earlier_meshes(deform_model):
    """A shift in mesh space is skinned afterward; published earlier poses and imported bind geometry stay immutable."""
    data = deform_model().load()
    base = data.mesh_info[0].mesh
    source = data.mesh_info[0].deformation
    bones = (renpy.display.render.IDENTITY, renpy.display.matrix.Matrix.offset(.8, 0, 0))
    first = assimp._deform_geometry(source, (0, 0), bones)
    saved_points = first.get_points()
    shifted = assimp._deform_geometry(source, (1, 0), bones)
    assert [p[0] for p in shifted.get_points()] == pytest.approx([0, 1.6, 1.6, 0])
    assert shifted.bounds == pytest.approx((0, -.4, 0, 1.6, .4, 0))
    assert first.get_points() == saved_points
    assert [p[0] for p in base.get_points()] == pytest.approx([-.4, .4, .4, -.4])
    unskinned = assimp._deform_geometry(source, (1, 0), ())
    assert [p[0] for p in unskinned.get_points()] == pytest.approx([0, .8, .8, 0])


def test_deformation_rejects_a_skin_missing_joint_matrices(deform_model):
    """The pose check uses the importer's recorded joint maximum rather than rescanning every influence."""
    source = deform_model().load().mesh_info[0].deformation
    assert source.max_joint_index == 1
    with pytest.raises(ValueError, match="joint index"):
        assimp._deform_geometry(source, (0, 0), (renpy.display.render.IDENTITY,))


def test_deformation_names_an_invalid_color_accessor(deform_model):
    """An out-of-range COLOR_0 reports the glTF accessor instead of an IndexError."""
    def broken(doc):
        doc["meshes"][0]["primitives"][0]["attributes"]["COLOR_0"] = 99

    with pytest.raises(ValueError, match="color accessor"):
        deform_model(broken).load()


@pytest.mark.parametrize("zoom", [0, -1.0, math.nan, math.inf])
def test_model_rejects_unusable_zoom(zoom):
    """A zero, negative or non-finite zoom would otherwise turn every vertex into NaN or a point."""
    with pytest.raises(ValueError, match="zoom"):
        assimp.GLTFModel("room.glb", shader="renpy.texture", zoom=zoom)


def test_inactive_scene_morph_animation(deform_model):
    """A clip may animate a mesh in another scene without moving the active scene's geometry."""
    def other_scene(doc):
        node = len(doc["nodes"])
        doc["nodes"].append({"mesh": 0})
        doc["scenes"].append({"nodes": [node]})
        doc["animations"][1]["channels"][0]["target"]["node"] = node

    asset = deform_model(other_scene).load().asset
    assert asset.clip_changes("Expand", 1.0, False) == {}
    assert asset.info()["animations"][1]["duration"] > 0


def test_clip_blend_restores_missing_channels_from_bind_pose(deform_model):
    """A morph-only secondary clip blends joint translation back toward bind pose rather than leaving the primary joint fully bent."""
    asset = deform_model().load().asset
    matrices, visible, weights = asset.pose((), "Bend", 2, False, 100, ("Expand", .5, 2), True)
    assert matrices[4].transform(0, 0, 0, components=3) == pytest.approx((40, 0, 0))
    assert weights[1] == (.5, 0)
    assert visible[1]
    matrices, _visible, weights = asset.pose((), None, 0, False, 100, ("Expand", 1, 1), True)
    assert matrices[4].transform(0, 0, 0, components=3) == (0, 0, 0)
    assert weights[1] == (.5, 0)


def test_deformation_large_mesh_bounds_and_cpu_cost(deform_model):
    """A 16K-vertex pose exercises the native loop and bounds; timing is reported without a machine-dependent pass threshold."""
    import array
    import copy
    import statistics
    import time

    source = copy.copy(deform_model().load().mesh_info[0].deformation)
    repeats = 4096
    count = source.count
    source.positions = source.positions * repeats
    source.attributes = source.attributes * repeats
    source.handedness = source.handedness * repeats
    source.targets = source.targets[:count * 9] * repeats + source.targets[count * 9:] * repeats
    source.joint_indices = source.joint_indices * repeats
    source.joint_weights = source.joint_weights * repeats
    source.count *= repeats
    source.joint_starts = array.array("I", range(source.count + 1))
    source.triangles = array.array("I", (index + block * count for block in range(repeats) for index in source.triangles))
    timings = []
    for frame in range(20):
        bones = (IDENTITY, renpy.display.matrix.Matrix.offset(.8 * frame / 19, 0, 0))
        started = time.perf_counter()
        mesh = assimp._deform_geometry(source, (.5, .25), bones)
        timings.append((time.perf_counter() - started) * 1000)
    assert mesh.bounds == pytest.approx((-.2, -.4, 0, 1.4, .4, 0))
    assert len(mesh.get_points()) == 16384
    print(json.dumps({"vertices": source.count, "morph_targets": 2, "joints": 2,
        "median_cpu_ms": statistics.median(timings), "max_cpu_ms": max(timings)}))


def test_morph_overrides_are_independent_of_shared_bind_pose(deform_model):
    """Node-level weights override animated values without mutating the source mesh defaults or marking its matrix changed."""
    asset = deform_model().load().asset
    overrides = renpy.gl2.gltfscene.node_overrides({"Pane": {"weights": [-.5, 2]}})
    matrices, _visible, weights = asset.pose(overrides, "Expand", 2, False, 100, include_weights=True)
    assert weights[1] == (-.5, 2)
    assert matrices[1].transform(0, 0, 0, components=3) == (9900, 0, 0)
    assert asset.pose((), None, 0, False, 100, include_weights=True)[2][1] == (0, 0)


def test_deformation_uses_the_native_default_material_beside_unused_materials(deform_model):
    """Assimp imports only referenced materials, so its default material can sit below the authored material count."""
    def edit(document):
        document["materials"].insert(0, {"name": "Unused"})
        del document["meshes"][0]["primitives"][0]["material"]
    mesh_info = deform_model(edit).load().mesh_info[0]
    assert mesh_info.material_index == 2
    assert mesh_info.uniforms["u_color_base"] == (1, 1, 1, 1)


def test_deformation_keeps_a_rest_pose_triangle_with_zero_area(deform_model):
    """A morph can open a triangle that is collapsed at rest; Assimp must not split it into a line primitive."""
    def edit(document):
        data = bytearray(base64.b64decode(document["buffers"][0]["uri"].partition(",")[2]))
        start = document["bufferViews"][document["accessors"][0]["bufferView"]].get("byteOffset", 0)
        data[start + 24:start + 36] = data[start + 12:start + 24]
        document["buffers"][0]["uri"] = "data:application/octet-stream;base64," + base64.b64encode(bytes(data)).decode()
    data = deform_model(edit).load()
    assert len(data.mesh_info[0].mesh.get_points()) == 4


def test_camera_keeps_a_source_that_is_not_drawn_predicted(deform_model, mocker):
    """A source used only for its camera would otherwise count as inactive and be evicted after each prediction."""
    source = deform_model()
    camera = assimp.GLTFCamera(source, renpy.display.layout.Null())
    mocker.patch.object(assimp, "new_predicted", set())
    mocker.patch.object(renpy.display.render, "render", return_value=Render(1, 1))
    camera.render(400, 300, 0, 0)
    assert source in assimp.new_predicted


def test_camera_letterbox_offsets_events(deform_model, mocker):
    """A 4:3 camera centered in 400x200 must give its child events in the child's own coordinates."""
    seen = []

    class Child(renpy.display.layout.Null):
        def event(self, ev, x, y, st):
            seen.append((x, y))

    camera = assimp.GLTFCamera(deform_model(), Child())
    mocker.patch.object(assimp, "new_predicted", set())
    mocker.patch.object(renpy.display.render, "render", return_value=Render(1, 1))
    camera.render(400, 200, 0, 0)
    camera.event(None, 200, 100, 0)
    assert seen == [pytest.approx((400 / 3, 100), abs=1)]


def test_scene_predicts_environment_and_shadow_passes_without_inherited_parts(mocker):
    """Environment filtering and shadow tiles draw in their own targets, never under cascades or peeling."""
    predict = mocker.patch.object(renpy.gl2.gl2shadercache, "predict_shader")
    scene = assimp.GLTFScene(renpy.display.layout.Null(), environment="room.png", shadow_cascades=2, transparency_layers=2)
    scene.predict_shaders(())
    predicted = [call.args[0] for call in predict.call_args_list]
    assert ("renpy.gltf.environment",) in predicted
    assert ("renpy.gltf.shadow",) in predicted and ("renpy.gltf.shadow_copy",) in predicted
    predict.reset_mock()
    assimp.GLTFScene(renpy.display.layout.Null(), shadows=False).predict_shaders(())
    predicted = [call.args[0] for call in predict.call_args_list]
    assert ("renpy.gltf.shadow",) not in predicted and ("renpy.gltf.shadow_copy",) not in predicted


def append_deformation_accessor(document, values, kind, component=5126):
    """Add one small independent data URI, keeping source vertex identities and native file I/O intact."""
    formats = {5126: "f", 5121: "B"}
    data = struct.pack("<" + formats[component] * len(values), *values)
    document["buffers"].append({"byteLength": len(data), "uri": "data:application/octet-stream;base64," + base64.b64encode(data).decode()})
    document["bufferViews"].append({"buffer": len(document["buffers"]) - 1, "byteLength": len(data)})
    document["accessors"].append({"bufferView": len(document["bufferViews"]) - 1, "componentType": component,
        "count": len(values) // {"SCALAR": 1, "VEC4": 4}[kind], "type": kind})
    return len(document["accessors"]) - 1


def test_skin_preserves_more_than_four_influences_and_optional_bind_matrices(deform_model):
    """JOINTS_1 contributes to every vertex; an omitted inverse-bind stream means identity rather than vendor limits."""
    def edit(document):
        document["nodes"].extend({"name": "Bone"} for _ in range(4))
        document["nodes"][2]["children"].extend([6, 7, 8, 9])
        document["skins"][0]["joints"].extend([6, 7, 8, 9])
        document["skins"][0].pop("inverseBindMatrices")
        attributes = document["meshes"][0]["primitives"][0]["attributes"]
        for name, values, component in (
            ("JOINTS_0", [0, 1, 2, 3] * 4, 5121), ("JOINTS_1", [4, 5, 0, 0] * 4, 5121),
            ("WEIGHTS_0", [1/6] * 16, 5126), ("WEIGHTS_1", [1/6, 1/6, 0, 0] * 4, 5126),
        ):
            attributes[name] = append_deformation_accessor(document, values, "VEC4", component)

    data = deform_model(edit).load()
    source = data.mesh_info[0].deformation
    bones = tuple(renpy.display.matrix.Matrix.offset(i, 0, 0) for i in range(6))
    mesh = assimp._deform_geometry(source, (0, 0), bones)
    assert [p[0] for p in mesh.get_points()] == pytest.approx([2.1, 2.9, 2.9, 2.1])


@pytest.mark.parametrize("mode, expected", [("STEP", 0), ("LINEAR", .25), ("CUBICSPLINE", .15625)])
def test_weight_animation_interpolates_complete_target_vectors(deform_model, mode, expected):
    """Flattened SCALAR streams group all morph targets per key, including Hermite tangent/value triplets."""
    def edit(document):
        sampler = document["animations"][1]["samplers"][0]
        sampler["interpolation"] = mode
        if mode == "CUBICSPLINE":
            sampler["output"] = append_deformation_accessor(document, [0, 0, 0, 0, 0, 0, 0, 0, 1, 0, 0, 0], "SCALAR")

    asset = deform_model(edit).load().asset
    assert asset.pose((), "Expand", .5, False, 100, include_weights=True)[2][1] == pytest.approx((expected, 0))


@pytest.mark.parametrize("edit, message", [
    (lambda d: d["skins"][0].update(joints=[3, 3]), "unique joint"),
    (lambda d: d["skins"][0].update(skeleton=4), "ancestor"),
    (lambda d: d["nodes"][1].update(weights=[1]), "target count"),
    (lambda d: d["meshes"][0]["primitives"][0]["attributes"].pop("WEIGHTS_0"), "joint/weight"),
    (lambda d: d["meshes"][0]["primitives"][0]["targets"][0].update(POSITION=10), "VEC3"),
])
def test_invalid_deformation_metadata_fails_before_render(deform_model, edit, message):
    """Malformed rigs and weight counts fail during loading, before native deformation or GPU upload."""
    with pytest.raises((ValueError, TypeError), match=message):
        deform_model(edit).load()


def test_blended_animation_controls_survive_old_and_new_save_shapes(deform_model):
    """Blend controls are immutable saved settings; old saved models retain the single-clip behavior."""
    model = deform_model(animation="Bend", animation_blend=("Expand", .75), animation_blend_duration=2,
        animation_blend_time=1)
    restored = pickle.loads(pickle.dumps(model))
    assert restored.animation_blend == ("Expand", .75)
    assert restored.animation_blend_duration == 2 and restored.animation_blend_time == 1
    for name in ("animation_blend", "animation_blend_duration", "animation_blend_time"):
        delattr(model, name)
    restored = pickle.loads(pickle.dumps(model))
    assert restored.animation_blend is None and restored.animation_blend_duration == 0


@pytest.mark.parametrize("options", [
    {"scene": 1}, {"node_overrides": []}, {"node_overrides": {"Door": {"visible": 1}}},
    {"node_overrides": {"Door": {"translation": (0, math.inf, 0)}}},
    {"node_overrides": {"Door": {"rotation": (0, 0, 0, 0)}}},
    {"node_overrides": {"Door": {"unknown": 1}}}, {"material_overrides": []},
    {"material_overrides": {"Paint": {"color": (1, 0, 0, 1)}}},
    {"animation": True}, {"animation_loop": 1}, {"animation_time": math.nan},
    {"animation_speed": math.inf}, {"animation_offset": "1"},
])
def test_scene_rejects_invalid_instance_settings(options):
    """Invalid instance settings fail during construction, before prediction or GPU resource allocation."""
    with pytest.raises((ValueError, TypeError)):
        assimp.GLTFModel("unread.gltf", material=True, **options)
