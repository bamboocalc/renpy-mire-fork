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

import itertools
import math
import pickle
from types import SimpleNamespace

import pytest

import renpy
from renpy.display.matrix import Matrix
from renpy.display.render import Render
from renpy.gl2 import assimp, gltf


@pytest.mark.parametrize("options", [
    {"kind": "area"}, {"intensity": -1}, {"intensity": math.inf}, {"intensity": "1"},
    {"direction": (0, 0, 0)}, {"color": (-1, 0, 0)}, {"range": 0}, {"range": -1},
    {"kind": "directional", "range": 10}, {"inner_cone_angle": 1, "outer_cone_angle": 0.5},
    {"outer_cone_angle": math.pi}, {"cast_shadows": 1},
])
def test_light_rejects_invalid_parameters(options):
    """Invalid light data fails before matrix construction, division, or shader upload."""
    with pytest.raises(ValueError):
        gltf.GLTFLight(**options)


def light_document():
    """Two light instances share a definition, but only the selected scene is active."""
    return {"extensions": {"KHR_lights_punctual": {"lights": [
        {"type": "spot", "name": "Lamp", "intensity": 4, "range": 10, "spot": {}}
    ]}}, "nodes": [
        {"translation": [1, 2, 3], "scale": [2, 2, 2], "children": [1]},
        {"translation": [0, 0, 1], "extensions": {"KHR_lights_punctual": {"light": 0}}},
        {"translation": [10, 0, 0], "extensions": {"KHR_lights_punctual": {"light": 0}}},
    ], "scenes": [{"nodes": [2]}, {"nodes": [0]}], "scene": 1}


def test_imported_light_inherits_nodes_and_basis_without_scaling_range():
    """Parent scale moves the node, model zoom converts coordinates, and the physical range remains ten meters."""
    lights = gltf.import_lights(light_document(), 100)
    assert len(lights) == 1
    light, matrix = lights[0]
    assert matrix.transform(*light.position, components=3) == pytest.approx((100, -200, 500))
    assert light.range == 10 and light.intensity == 4
    assert light.light_name == "Lamp"


def test_node_matrix_supports_column_major_and_quaternions():
    """glTF's column-major matrices and XYZW rotations produce the same scene positions as TRS."""
    matrix = gltf.node_matrix({"matrix": [2, 0, 0, 0, 0, 3, 0, 0, 0, 0, 4, 0, 1, 2, 3, 1]})
    assert matrix.transform(1, 1, 1, components=3) == pytest.approx((3, 5, 7))
    rotation = gltf.node_matrix({"rotation": [0, math.sin(math.pi / 4), 0, math.cos(math.pi / 4)]})
    assert rotation.transform(0, 0, -1, components=3) == pytest.approx((-1, 0, 0), abs=1e-6)


@pytest.mark.parametrize("case", ["cycle", "node-index", "light-index", "rotation", "matrix-trs", "scene-index"])
def test_import_rejects_invalid_scene_graph(case):
    """The active light graph is validated before passing a malformed scene to Assimp."""
    document = light_document()
    if case == "cycle":
        document["nodes"][1]["children"] = [0]
    elif case == "node-index":
        document["nodes"][0]["children"] = [8]
    elif case == "light-index":
        document["nodes"][1]["extensions"]["KHR_lights_punctual"]["light"] = 2
    elif case == "rotation":
        document["nodes"][0]["rotation"] = [0, 0, 0, 0]
    elif case == "matrix-trs":
        document["nodes"][0]["matrix"] = [1, 0, 0, 0] * 4
    else:
        document["scene"] = 10
    with pytest.raises(ValueError):
        gltf.import_lights(document, 100)


def test_collection_matches_render_offset_order():
    """Render applies a child's layout offset before its parent's reverse matrix; lights must follow the same order."""
    light = gltf.GLTFLight(position=(1, 0, 0))
    root = Render(400, 300)
    root.reverse = Matrix.scale(2, 2, 2)
    root.blit(light.render(0, 0, 0, 0), (3, 0))
    meshes, lights = gltf.collect_scene(root, 400, 300)
    assert meshes == []
    assert lights[0][1] == pytest.approx((5, 0, 0))


@pytest.mark.parametrize("kind", [0, 1])
def test_camera_boundaries_do_not_move_lights(kind):
    """Projection and view matrices move the camera while keeping light positions in world space."""
    light = gltf.GLTFLight(position=(2, 3, 4))
    root = Render(400, 300)
    root.reverse = Matrix.offset(100, 0, 0)
    root.matrix_kind = renpy.gl2.gl2draw.MATRIX_KINDS[kind]
    root.blit(light.render(0, 0, 0, 0), (0, 0))
    _meshes, lights = gltf.collect_scene(root, 400, 300)
    assert lights[0][1] == pytest.approx((2, 3, 4))


@pytest.mark.parametrize("scale, maximum", [(1, (2048, 2048)), (1.25, (1024, 1024)), (1.25, (512, 400))])
def test_guard_preserves_camera_framing_and_world_coordinates(mocker, scale, maximum):
    """Drawable-pixel calibration preserves world lights and visible projection under fractional DPI and texture-limit shrinkage."""
    from renpy.gl2.gl2mesh3 import Mesh3

    mocker.patch.object(renpy.display, "draw", SimpleNamespace(virt_to_draw=Matrix.scale(scale, scale, 1),
        texture_loader=SimpleNamespace(max_texture_width=maximum[0], max_texture_height=maximum[1])))
    child = Render(400, 300)
    child.reverse = Matrix.perspective(400, 300, 1, 500, 10000)
    child.forward = child.reverse.inverse()
    child.matrix_kind = renpy.gl2.gl2draw.MATRIX_KINDS[1]
    mesh = Render(0, 0)
    mesh.mesh = Mesh3.rectangle(-1, -1, 1, 1)
    mesh.reverse = Matrix.offset(200, 150, 0)
    mesh.forward = mesh.reverse.inverse()
    child.blit(mesh, (0, 0))
    light = gltf.GLTFLight(position=(2, 3, 4))
    child.blit(light.render(0, 0, 0, 0), (0, 0))
    original, old_lights = gltf.collect_scene(child, 400, 300)
    expanded, mask, region = gltf.refraction_guard(child, .25)
    records, lights = gltf.collect_scene(expanded, *expanded.get_size())
    assert lights[0][1] == old_lights[0][1]
    assert gltf.matrix_values(records[0][1]) == gltf.matrix_values(original[0][1])
    for point in ((0, 0, 0), (.75, -.5, 1)):
        first = (original[0][3] * original[0][2] * original[0][1]).transform(*point, components=4)
        second = (records[0][3] * records[0][2] * records[0][1]).transform(*point, components=4)
        expected = tuple((first[i] / first[3] + 1) / 2 for i in (0, 1))
        actual = tuple(((second[i] / second[3] + 1) / 2 - region[i]) / region[i + 2] for i in (0, 1))
        assert actual == pytest.approx(expected)
        assert second[2] / second[3] == pytest.approx(first[2] / first[3])
    assert expanded.width <= maximum[0] and expanded.height <= maximum[1]
    assert mask is not None


def test_per_frame_scene_walks_leave_no_cycles(mocker):
    """A recursive closure held each frame's mesh Renders, matrices and dicts until a garbage collection stalled a frame."""
    import gc
    from types import FunctionType

    mocker.patch.object(renpy.display, "draw", SimpleNamespace(virt_to_draw=Matrix.scale(1, 1, 1),
        texture_loader=SimpleNamespace(max_texture_width=2048, max_texture_height=2048)))
    child = Render(400, 300)
    child.blit(Render(10, 10), (0, 0))
    gc.collect()
    gc.disable()
    try:
        gltf.collect_scene(child, 400, 300)
        gltf.refraction_guard(child, .25)
        leaked = {i.__qualname__ for i in gc.get_objects() if type(i) is FunctionType
            and i.__qualname__.startswith(("collect_scene.<locals>", "refraction_guard.<locals>"))}
    finally:
        gc.enable()
    assert not leaked


def test_point_light_ignores_a_collapsed_direction_axis():
    """Node scale does not give a point light an orientation, even if it collapses the unused direction vector."""
    light = gltf.GLTFLight(position=(0, 0, 1))
    root = Render(400, 300)
    root.reverse = Matrix.scale(1, 1, 0)
    root.blit(light.render(0, 0, 0, 0), (0, 0))
    _meshes, lights = gltf.collect_scene(root, 400, 300)
    assert lights[0][1] == (0, 0, 0)


@pytest.mark.parametrize("kind", ["builtin", "perspective", "orthographic", "infinite"])
def test_cascades_cover_camera_intervals_and_overlap(kind):
    """Every slice covers its camera interval and the previous slice's transition, including infinite far planes."""
    from renpy.gl2 import gltfscene

    view = renpy.display.render.IDENTITY
    if kind == "builtin":
        projection = Matrix.perspective(400, 300, 1, 500, 10000)
    else:
        definition = {"type": "orthographic", "orthographic": {"xmag": 2, "ymag": 1.5, "znear": .01, "zfar": 100}}
        if kind != "orthographic":
            definition = {"type": "perspective", "perspective": {"yfov": 1, "znear": .01}}
            if kind != "infinite":
                definition["perspective"]["zfar"] = 100
        definition = gltfscene.camera_definitions({"cameras": [definition]})[0]
        view, projection, _viewport = gltfscene.camera_matrices(definition, Matrix.scale(100, -100, 100), 400, 300, 100)
    projection = Matrix.texture_projection(400, 300) * projection
    records = [(None, None, view, projection, {}, {"gltf_camera_projection": True})]
    eye, forward, limits, frusta = gltf.cascade_frusta(records, 4, 20, .01)
    if kind == "builtin":
        assert eye == pytest.approx((200, 150, 500), abs=.01)
    assert limits[-1] == pytest.approx(2000, rel=1e-5)
    assert all(a < b for a, b in itertools.pairwise(limits))
    for level, points in enumerate(frusta):
        depths = [gltf.dot(tuple(a - b for a, b in zip(point, eye)), forward) for point in points]
        assert max(depths) == pytest.approx(limits[level + 1])
        assert min(depths) < limits[level] if level else min(depths) == pytest.approx(limits[0])
        matrix = gltf.cascade_matrix(points, points, (-1, -1, -1), 512, 1)
        assert all(abs(v) <= 1.00001 for point in points for v in matrix.transform(*point, components=3))


def test_cascade_grid_stays_fixed_under_small_camera_translation():
    """Moving a camera less than a shadow texel leaves a stable light-space grid instead of shimmering."""
    points = tuple((x, y, z) for x in (-10, 10) for y in (-10, 10) for z in (-10, 10))
    moved = tuple((x + .00001, y, z) for x, y, z in points)
    first = gltf.cascade_matrix(points, points, (0, 0, -1), 512, 1)
    second = gltf.cascade_matrix(moved, points, (0, 0, -1), 512, 1)
    assert gltf.matrix_values(first) == gltf.matrix_values(second)


def test_light_override_replaces_without_mutating_shared_import():
    """Per-model overrides must not change the light definition used by another model instance."""
    light = gltf.GLTFLight(intensity=4, color=(1, 1, 1))
    changed = light.replaced((("intensity", 0), ("color", (1, 0, 0))))
    assert changed.intensity == 0 and changed.color == (1, 0, 0)
    assert light.intensity == 4 and light.color == (1, 1, 1)


def test_light_count_has_an_explicit_limit():
    """Exceeding the supported active-light count raises instead of truncating the shader arrays."""
    light = gltf.GLTFLight()
    with pytest.raises(ValueError, match="at most"):
        gltf.light_uniforms([(light, light.position, light.direction)] * 9, 1)


def test_old_scene_shape_uses_compatibility_defaults():
    """A scene saved before these features preserves its old lighting and does not acquire shadows or refraction."""
    scene = assimp.GLTFScene(renpy.display.layout.Null())
    for name in ("lights", "import_lights", "meters_per_unit", "shadows", "shadow_size", "shadow_bias", "transmission",
                 "shadow_cache", "depth_output", "refraction_batch", "transparency_layers", "refraction_margin",
                 "shadow_cascades", "shadow_distance", "colored_shadows", "caustics", "tone_mapping"):
        delattr(scene, name)
    restored = pickle.loads(pickle.dumps(scene))
    assert not restored.import_lights and not restored.shadows and not restored.transmission
    assert restored.lights == () and restored.meters_per_unit == 1
    assert restored.shadow_cache
    assert not restored.depth_output and restored.refraction_batch
    assert restored.transparency_layers == 0 and restored.refraction_margin == 0
    assert restored.shadow_cascades == 1 and restored.shadow_distance == 50
    assert not restored.colored_shadows
    assert not restored.caustics
    assert restored.tone_mapping == "reinhard"


def test_scene_and_light_options_survive_pickling():
    """Light and scene settings remain plain saved data without embedding a render, framebuffer, or native mesh."""
    scene = assimp.GLTFScene(renpy.display.layout.Null(),
        lights=(gltf.GLTFLight("spot", position=(2, 3, 4), intensity=8, range=10),),
        meters_per_unit=0.01, shadow_size=256, shadow_bias=0.001, shadow_cache=False,
        shadow_cascades=4, shadow_distance=25, caustics=True, tone_mapping=None)
    restored = pickle.loads(pickle.dumps(scene))
    assert restored.tone_mapping is None
    assert restored.shadows and restored.transmission and restored.import_lights
    assert not restored.shadow_cache
    assert restored.shadow_cascades == 4 and restored.shadow_distance == 25
    assert restored.colored_shadows
    assert restored.caustics
    assert restored.meters_per_unit == 0.01 and restored.shadow_size == 256 and restored.shadow_bias == 0.001
    assert restored.lights[0].position == (2, 3, 4) and restored.lights[0].intensity == 8


def test_depth_of_field_scene_survives_pickling():
    """Depth effects save only the scene and optical parameters; renderer textures remain outside the save."""
    scene = assimp.GLTFScene(renpy.display.layout.Null(), depth_output=True)
    effect = assimp.GLTFDepthOfField(scene, focus_distance=3.0, focus_range=0.4, max_blur=12)
    restored = pickle.loads(pickle.dumps(effect))
    assert restored.focus_distance == 3.0 and restored.focus_range == 0.4 and restored.max_blur == 12
    assert restored.child.depth_output
    assert restored.visit() == [restored.child]


def test_depth_of_field_accepts_an_image_name(mocker):
    """An image name reaches the scene's depth properties, which the image reference's wrapper render lacks."""
    depth = Render(4, 4)

    class Scene(renpy.display.displayable.Displayable):
        def render(self, width, height, st, at):
            rv = Render(4, 4)
            rv.add_uniform("u_gltf_scene_depth", depth)
            rv.add_uniform("u_gltf_depth_inverse", Matrix.scale(1, 1, 1))
            rv.add_uniform("u_gltf_depth_perspective", 0.0)
            rv.add_uniform("u_gltf_depth_scale", 1.0)
            rv.add_uniform("u_gltf_exposure", 1.0)
            rv.add_uniform("u_gltf_tone_mapping", 0.0)
            return rv

    plain = lambda d, width, height, st, at: d.render(width, height, st, at)
    mocker.patch.object(renpy.display.render, "render", plain)
    mocker.patch.object(renpy.display.image, "render", plain)
    mocker.patch.dict(renpy.display.image.images, {("dof_room",): Scene()})
    rv = assimp.GLTFDepthOfField("dof_room").render(4, 4, 0, 0)
    assert rv.uniforms["u_gltf_scene_depth"] is depth
    assert rv.uniforms["u_gltf_tone_mapping"] == 0.0


def test_camera_selection_and_shared_source_survive_pickling():
    """A camera saves source selection and controls while keeping its default child tied to the same restored model."""
    source = assimp.GLTFModel("unread.gltf", scene=True, animation="Pan")
    camera = assimp.GLTFCamera(source, camera="Main", node=4)
    restored = pickle.loads(pickle.dumps(camera))
    assert restored.camera == "Main" and restored.node == 4
    assert restored.child is restored.source and restored.source.animation == "Pan"


@pytest.mark.parametrize("options", [{"focus_distance": 0}, {"focus_range": -1}, {"max_blur": 33},
    {"focus_distance": math.nan}, {"max_blur": math.inf}])
def test_depth_of_field_rejects_invalid_settings(options):
    """Optical settings are checked before division, shader compilation or GPU allocation."""
    with pytest.raises(ValueError):
        assimp.GLTFDepthOfField(renpy.display.layout.Null(), **options)


def test_scene_accepts_a_light_generator():
    """Validation must not consume an iterable before the scene stores its lights."""
    light = gltf.GLTFLight(intensity=4)
    scene = assimp.GLTFScene(renpy.display.layout.Null(), lights=(item for item in (light,)))
    assert scene.lights == (light,)


def test_shadow_cache_evicts_least_recent_texture():
    """A hit keeps a face warm, and an insertion releases the oldest face within the byte budget."""
    cache = gltf.ShadowCache(16)
    scene = assimp.GLTFScene(renpy.display.layout.Null())
    owner = cache.owner(scene)
    textures = [SimpleNamespace(texture_width=1, texture_height=1) for _ in range(3)]
    for slot in (0, 1):
        cache.store((cache.epoch, owner, slot, (slot,)), textures[slot])
    assert cache.get(owner, 0, (0,)) is textures[0]
    cache.store((cache.epoch, owner, 2, (2,)), textures[2])
    assert cache.get(owner, 1, (1,)) is None
    assert cache.get(owner, 0, (0,)) is textures[0]
    assert cache.get(owner, 2, (2,)) is textures[2]
    assert cache.snapshot()["retained_bytes"] == 16


def test_shadow_cache_clear_rejects_pending_old_publication():
    """A render prepared before a context reset cannot repopulate its new cache afterward."""
    cache = gltf.ShadowCache(16)
    scene = assimp.GLTFScene(renpy.display.layout.Null())
    owner = cache.owner(scene)
    token = (cache.epoch, owner, 0, (1,))
    cache.clear()
    cache.store(token, SimpleNamespace(texture_width=1, texture_height=1))
    assert cache.get(owner, 0, (1,)) is None


def test_shadow_cache_accounts_for_hdr_and_depth_together():
    """Colored targets must evict at their full HDR plus depth allocation instead of exceeding the retention budget."""
    cache = gltf.ShadowCache(24)
    scene = assimp.GLTFScene(renpy.display.layout.Null())
    owner = cache.owner(scene)
    textures = [SimpleNamespace(texture_width=1, texture_height=1, properties={"allocated_size": 8},
        depth_texture=SimpleNamespace(properties={"allocated_size": 4})) for _ in range(3)]
    for slot, texture in enumerate(textures):
        cache.store((cache.epoch, owner, slot, slot), texture)
    assert cache.get(owner, 0, 0) is None
    assert cache.get(owner, 1, 1) is textures[1] and cache.get(owner, 2, 2) is textures[2]
    assert cache.snapshot()["retained_bytes"] == 24


def test_shadow_cache_releases_dead_scene_and_oversized_replacement():
    """GPU references live only while their scene and current budget justify retaining them."""
    import gc

    cache = gltf.ShadowCache(8)
    scene = assimp.GLTFScene(renpy.display.layout.Null())
    owner = cache.owner(scene)
    texture = SimpleNamespace(texture_width=1, texture_height=1)
    cache.store((cache.epoch, owner, 0, (1,)), texture)
    cache.store((cache.epoch, owner, 0, (2,)), SimpleNamespace(texture_width=2, texture_height=1))
    assert cache.get(owner, 0, (1,)) is None
    cache.store((cache.epoch, owner, 0, (1,)), texture)
    del scene
    gc.collect()
    assert owner() is None and cache.snapshot()["retained_bytes"] == 0


@pytest.mark.parametrize("change", ["position", "points", "attributes", "indices", "untracked"])
def test_shadow_signature_tracks_geometry_changes(change):
    """Small movement and every mesh buffer revision invalidate maps; untracked native memory bypasses reuse."""
    from renpy.gl2.gl2mesh3 import Mesh3

    render = Render(0, 0)
    render.mesh = mesh = Mesh3.rectangle(-1, -1, 1, 1)
    mesh.point_version = mesh.attribute_version = mesh.triangle_version = 1
    record = (render, Matrix.offset(0, 0, 0), None, None, {}, {})
    original = gltf.shadow_signature(record, False)
    if change == "position":
        record = (render, Matrix.offset(0.00001, 0, 0), None, None, {}, {})
    elif change == "points":
        mesh.point_version += 1
    elif change == "attributes":
        mesh.attribute_version += 1
    elif change == "indices":
        mesh.triangle_version += 1
    else:
        mesh.point_version = 0
    assert gltf.shadow_signature(record, False) != original


@pytest.mark.parametrize("alpha_mode", [1.0, 0.75])
def test_shadow_signature_ignores_rgb_but_rejects_dynamic_cutout(alpha_mode):
    """Opaque recoloring reuses depth; all modes the shader treats as MASK must reevaluate Render-backed alpha."""
    from renpy.gl2.gl2mesh3 import Mesh3

    render = Render(0, 0)
    render.mesh = mesh = Mesh3.rectangle(-1, -1, 1, 1)
    mesh.point_version = mesh.attribute_version = mesh.triangle_version = 1
    uniforms = {"u_color_base": (1, 0, 0, 1)}
    record = (render, Matrix.offset(0, 0, 0), None, None, uniforms, {})
    original = gltf.shadow_signature(record, False)
    uniforms["u_color_base"] = (0, 1, 0, 0)
    assert gltf.shadow_signature(record, False) == original
    uniforms.update(u_material_gltf_alphamode=alpha_mode, u_tex_base_color=Render(1, 1))
    assert gltf.shadow_signature(record, False) is None


def test_shadow_signature_freezes_mutable_sampler_settings():
    """Changing a list-valued sampler cannot also change the key of an already retained map."""
    from renpy.gl2.gl2mesh3 import Mesh3

    render = Render(0, 0)
    render.mesh = mesh = Mesh3.rectangle(-1, -1, 1, 1)
    mesh.point_version = mesh.attribute_version = mesh.triangle_version = 1
    wrapping = [renpy.uguu.GL_REPEAT, renpy.uguu.GL_REPEAT]
    record = (render, Matrix.offset(0, 0, 0), None, None, {}, {"texture_wrap": wrapping})
    original = gltf.shadow_signature(record, False)
    wrapping[0] = renpy.uguu.GL_CLAMP_TO_EDGE
    assert gltf.shadow_signature(record, False) != original


@pytest.mark.parametrize("enabled, shadow_light", [(True, False), (False, True)])
def test_shadow_atlas_skips_mesh_bounds_without_shadow_cameras(mocker, enabled, shadow_light):
    """Disabled shadows, or no shadow-casting light, need no scene bounds; each mesh's corners cost eight transforms."""
    corners = mocker.patch.object(gltf, "mesh_corners", return_value=[(0, 0, 0)])
    lights = [(gltf.GLTFLight("directional"), (0, 0, 0), (0, 0, -1))] if shadow_light else []
    gltf.shadow_uniforms([(None, None, None, None, {}, {})] * 3, lights, 512, 0.0, 0.01, enabled, False)
    assert not corners.called
