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


import collections
import json
import math
import struct
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import pytest

import renpy
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


def test_zero_budget_preserves_existing_eviction_policy(model_cache):
    """With retention disabled, inactive models are discarded and predicted models remain."""
    old = object()
    active = assimp.GLTFModel("active.glb", shader="renpy.texture")
    model_cache[old] = SimpleNamespace(memory_size=1)
    model_cache[active] = SimpleNamespace(memory_size=1)
    assimp.predicted.add(active)
    assimp.preload()
    assert list(model_cache) == [active]


def test_loading_an_inactive_model_promotes_it_before_eviction(model_cache, mocker):
    """Revisiting an older room retains it instead of a room that has not been used as recently."""
    older = assimp.GLTFModel("older.glb", shader="renpy.texture")
    recent = assimp.GLTFModel("recent.glb", shader="renpy.texture")
    for model in (older, recent):
        model_cache[model] = SimpleNamespace(memory_size=3 * 1024 * 1024)
    mocker.patch.object(renpy.config, "gltf_model_cache_size_mb", 4)
    data = model_cache[older]
    assert older.load() is data
    assimp.preload()
    assert list(model_cache) == [older]
    assert model_cache[older] is data


def test_predicted_models_do_not_consume_the_inactive_budget(model_cache, mocker):
    """A predicted room larger than the budget does not evict an inactive room that fits."""
    inactive = object()
    active = assimp.GLTFModel("active.glb", shader="renpy.texture")
    model_cache[inactive] = SimpleNamespace(memory_size=3 * 1024 * 1024)
    model_cache[active] = SimpleNamespace(memory_size=8 * 1024 * 1024)
    assimp.predicted.add(active)
    mocker.patch.object(renpy.config, "gltf_model_cache_size_mb", 4)
    assimp.preload()
    assert set(model_cache) == {inactive, active}


def test_explicit_memory_clear_removes_predicted_and_retained_models(model_cache, mocker):
    """An explicit clear overrides both prediction and the optional retention budget."""
    inactive = object()
    active = assimp.GLTFModel("active.glb", shader="renpy.texture")
    for model in (inactive, active):
        model_cache[model] = SimpleNamespace(memory_size=1)
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
    assert model not in model_cache
    loader.load.side_effect = lambda data, *args: setattr(data, "mesh_info", [])
    assert model.load() is model_cache[model]


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
def test_native_import_keeps_geometry_with_and_without_tangents(model_cache, mocker, tmp_path, authored_tangents):
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
    plain = assimp.GLTFModel(model_path.as_posix(), shader="renpy.texture").load()
    tangent = assimp.GLTFModel(model_path.as_posix(), shader="renpy.texture", tangents=True).load()
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
    data = SimpleNamespace(memory_size=1, mesh_info=[])
    model_cache[model] = data
    with ThreadPoolExecutor(max_workers=1) as pool, assimp.loader_lock:
        result = pool.submit(getattr(model, operation)).result(timeout=1)
        if operation == "load":
            assert result is data
        else:
            assert result == []
