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
import math
import weakref

import renpy

MAX_LIGHTS = 8


def material_parts(uniforms):
    """Only active extension lobes add shader parts, keeping the core material's sampler requirement unchanged."""
    parts = []
    if (uniforms.get("u_material_specular_factor", 1) != 1
            or uniforms.get("u_material_specular_color_factor", (1, 1, 1)) != (1, 1, 1)
            or uniforms.get("u_material_specular_present", 0) or uniforms.get("u_material_specular_color_present", 0)):
        parts.append("renpy.gltf.specular")
    if uniforms.get("u_material_clearcoat_factor", 0) > 0:
        parts.append("renpy.gltf.clearcoat")
    if any(uniforms.get("u_material_sheen_color_factor", (0, 0, 0))):
        parts.append("renpy.gltf.sheen")
    return tuple(parts)


def refraction_region(mesh, transform, width, height):
    """Conservative drawable-pixel bounds; near-plane crossings require a fresh framebuffer copy."""
    bounds = mesh.bounds
    if bounds is None:
        return None
    points = [transform.transform(x, y, z, components=4)
        for x in (bounds[0], bounds[3]) for y in (bounds[1], bounds[4]) for z in (bounds[2], bounds[5])]
    if any(point[3] <= 0 for point in points):
        return None
    xs = [(x / w + 1) * width / 2 for x, y, z, w in points]
    ys = [(y / w + 1) * height / 2 for x, y, z, w in points]
    # Cover the linear texture-filter footprint and pixel alignment rounding.
    return min(xs) - 2, min(ys) - 2, max(xs) + 2, max(ys) + 2


class ShadowCache:
    """Renderer-owned LRU of immutable targets; weak ownership keeps GPU state out of saves."""

    def __init__(self, limit):
        self.limit = max(0, int(limit))
        self.entries = collections.OrderedDict()
        self.owners = weakref.WeakKeyDictionary()
        self.bytes = 0
        self.epoch = 0
        self.hits = self.misses = self.evictions = 0

    def owner(self, scene):
        reference = self.owners.get(scene)
        if reference is None:
            reference = weakref.ref(scene, self.discard)
            self.owners[scene] = reference
        return reference

    def discard(self, owner):
        for key in list(self.entries):
            if key[0] == owner:
                _signature, _texture, size = self.entries.pop(key)
                self.bytes -= size

    def clear(self):
        self.entries.clear()
        self.bytes = 0
        self.epoch += 1

    def get(self, owner, slot, signature):
        key = (owner, slot)
        entry = self.entries.get(key)
        if signature is not None and entry is not None and entry[0] == signature:
            self.entries.move_to_end(key)
            self.hits += 1
            return entry[1]
        self.misses += 1
        return None

    def store(self, token, texture):
        epoch, owner, slot, signature = token
        if epoch != self.epoch or owner() is None or signature is None:
            return
        properties = getattr(texture, "properties", {}) or {}
        size = properties.get("allocated_size", texture.texture_width * texture.texture_height * 8)
        depth = getattr(texture, "depth_texture", None)
        if depth is not None:
            size += depth.properties["allocated_size"]
        key = (owner, slot)
        old = self.entries.pop(key, None)
        if old is not None:
            self.bytes -= old[2]
        if size > self.limit:
            return
        while self.entries and self.bytes + size > self.limit:
            _key, (_signature, _texture, removed) = self.entries.popitem(last=False)
            self.bytes -= removed
            self.evictions += 1
        self.entries[key] = (signature, texture, size)
        self.bytes += size

    def snapshot(self):
        return {"hits": self.hits, "misses": self.misses, "evictions": self.evictions,
            "retained_bytes": self.bytes, "budget_bytes": self.limit, "textures": len(self.entries)}


def matrix_values(matrix):
    """Exact components avoid Matrix's approximate equality hiding a small caster movement."""
    return (matrix.xdx, matrix.xdy, matrix.xdz, matrix.xdw, matrix.ydx, matrix.ydy, matrix.ydz, matrix.ydw,
        matrix.zdx, matrix.zdy, matrix.zdz, matrix.zdw, matrix.wdx, matrix.wdy, matrix.wdz, matrix.wdw)


def shadow_signature(record, transmission, *, colored=False):
    """Only tracked geometry and the standard shadow shader's actual opacity inputs can be reused."""
    source, world, _view, _projection, uniforms, properties = record
    mesh = source.mesh
    if not (mesh.point_version and mesh.attribute_version and mesh.triangle_version):
        return None
    names = ["u_material_gltf_alphamode"]
    bindings = []
    if uniforms.get("u_material_gltf_alphamode", 0) > 0.5:
        names.extend(("u_material_gltf_alphacutoff",))
        bindings.append("base_color")
        alpha = uniforms.get("u_color_base", (1, 1, 1, 1))[3]
    else:
        alpha = 1.0
    if transmission and uniforms.get("u_material_transmission_factor", 0) > 0:
        names.extend(("u_material_transmission_factor", "u_material_metallicfactor"))
        bindings.extend(("transmission", "metalness"))
    values = []
    if colored:
        names.extend(("u_color_base", "u_material_ior", "u_material_thickness_factor",
            "u_material_attenuation_distance", "u_material_attenuation_color", "u_material_specular_factor",
            "u_material_specular_color_factor", "u_material_roughnessfactor"))
        bindings.extend(("base_color", "thickness", "specular", "specular_color"))
    for binding in bindings:
        texture = uniforms.get("u_tex_" + binding)
        if not isinstance(texture, renpy.gl2.gl2texture.GLTexture):
            return None
        values.append(texture)
        names.extend("u_material_" + binding + suffix for suffix in ("_texcoord", "_uv0", "_uv1"))
    for name in names:
        value = uniforms.get(name)
        if isinstance(value, (list, tuple)):
            if any(type(i) not in (int, float) for i in value):
                return None
            value = tuple(value)
        elif value is not None and type(value) not in (int, float):
            return None
        values.append(value)
    samplers = tuple((name, tuple(value) if isinstance(value, (list, tuple)) else value)
        for name, value in sorted(properties.items())
        if name.startswith(("texture_wrap", "texture_scaling")))
    return (mesh, mesh.point_version, mesh.attribute_version, mesh.triangle_version, mesh.points, mesh.triangles,
        matrix_values(world), alpha, tuple(values), samplers)


def number(value, name, minimum=None, maximum=None):
    """Validate values before converting them to native matrices or shader uniforms."""
    if type(value) not in (int, float) or not math.isfinite(value) or abs(value) > 3e38:
        raise ValueError(f"{name} must be a finite float32 number.")
    if (minimum is not None and value < minimum) or (maximum is not None and value > maximum):
        raise ValueError(f"{name} is outside its supported range.")
    return float(value)


def vector(value, name, size=3):
    if not isinstance(value, (tuple, list)) or len(value) != size:
        raise ValueError(f"{name} must contain {size} numbers.")
    return tuple(number(i, name) for i in value)


def unit(value):
    length = math.hypot(*value)
    if not length or not math.isfinite(length):
        raise ValueError("A light direction must be nonzero and finite.")
    return tuple(i / length for i in value)


class GLTFLight(renpy.display.displayable.Displayable):
    """
    :doc: assimp

    A directional, point, or spot light for a desktop :class:`GLTFScene`.
    Add it to the same camera/Fixed as the models, or to the scene's
    ``lights`` tuple. Transforms move and rotate the light with its parent.
    Recreate the displayable to change settings during script execution.

    `kind`
        ``"directional"``, ``"point"``, or ``"spot"``.

    `position`, `direction`
        Position in scene coordinates and the direction the light travels.
        The default direction is ``(0, 0, -1)``. Directional lights ignore
        position; point lights ignore direction.

    `color`, `intensity`
        Linear RGB and brightness. Intensity uses lux for directional
        lights and candela for point/spot lights. The scene's
        ``meters_per_unit`` converts positions to physical distances.

    `range`
        Optional positive cutoff distance in meters for point/spot lights.
        None gives inverse-square attenuation without a finite cutoff.

    `inner_cone_angle`, `outer_cone_angle`
        Spot cone half-angles in radians. Brightness fades between them.

    `cast_shadows`
        Whether the light participates in the scene's shadow atlas.

    `name`
        Optional name, used by imported-light overrides.
    """

    def __init__(self, kind="point", *, position=(0, 0, 0), direction=(0, 0, -1), color=(1, 1, 1),
                 intensity=1.0, range=None, inner_cone_angle=0.0, outer_cone_angle=math.pi / 4,
                 cast_shadows=True, name="", **properties):
        super().__init__(**properties)
        if kind not in ("directional", "point", "spot"):
            raise ValueError(f"Unknown glTF light kind: {kind!r}")
        self.kind = kind
        self.position = vector(position, "position")
        self.direction = unit(vector(direction, "direction"))
        self.color = vector(color, "color")
        if min(self.color) < 0:
            raise ValueError("Light color must be nonnegative.")
        self.intensity = number(intensity, "intensity", 0)
        for channel in self.color:
            number(channel * self.intensity, "light radiance", 0)
        self.range = None if range is None else number(range, "range", 0)
        if self.range == 0:
            raise ValueError("Light range must be positive.")
        if self.kind == "directional" and self.range is not None:
            raise ValueError("Directional lights do not have a range.")
        self.inner_cone_angle = number(inner_cone_angle, "inner_cone_angle", 0, math.pi / 2)
        self.outer_cone_angle = number(outer_cone_angle, "outer_cone_angle", 0, math.pi / 2)
        if self.inner_cone_angle >= self.outer_cone_angle:
            raise ValueError("The inner cone must be smaller than the outer cone.")
        if type(cast_shadows) is not bool or not isinstance(name, str):
            raise ValueError("cast_shadows must be a bool and name must be a string.")
        self.cast_shadows = cast_shadows
        self.light_name = name

    def replaced(self, overrides):
        values = {"kind": self.kind, "position": self.position, "direction": self.direction,
            "color": self.color, "intensity": self.intensity, "range": self.range,
            "inner_cone_angle": self.inner_cone_angle, "outer_cone_angle": self.outer_cone_angle,
            "cast_shadows": self.cast_shadows, "name": self.light_name}
        for key, value in overrides:
            if key not in ("color", "intensity", "range", "inner_cone_angle", "outer_cone_angle", "cast_shadows"):
                raise ValueError(f"Unsupported light override: {key!r}")
            values[key] = value
        return GLTFLight(**values)

    def render(self, width, height, st, at):
        rv = renpy.display.render.Render(0, 0)
        rv.add_property("gltf_lights", ((self, renpy.display.render.IDENTITY),))
        return rv


def node_matrix(node):
    """glTF stores matrices column-major and TRS rotations as XYZW quaternions."""
    Matrix = renpy.display.matrix.Matrix
    if "matrix" in node:
        if any(i in node for i in ("translation", "rotation", "scale")):
            raise ValueError("A glTF node cannot combine matrix and TRS.")
        values = vector(node["matrix"], "node matrix", 16)
        if values[3::4] != (0, 0, 0, 1):
            raise ValueError("A glTF node matrix must be affine.")
        return Matrix([values[c * 4 + r] for r in range(4) for c in range(4)])
    translation = vector(node.get("translation", (0, 0, 0)), "translation")
    scale = vector(node.get("scale", (1, 1, 1)), "scale")
    x, y, z, w = vector(node.get("rotation", (0, 0, 0, 1)), "rotation", 4)
    if abs(math.hypot(x, y, z, w) - 1) > 0.001:
        raise ValueError("A glTF rotation must be a unit quaternion.")
    rotation = Matrix([
        1 - 2 * (y*y + z*z), 2 * (x*y - z*w), 2 * (x*z + y*w), 0,
        2 * (x*y + z*w), 1 - 2 * (x*x + z*z), 2 * (y*z - x*w), 0,
        2 * (x*z - y*w), 2 * (y*z + x*w), 1 - 2 * (x*x + y*y), 0,
        0, 0, 0, 1])
    return Matrix.offset(*translation) * rotation * Matrix.scale(*scale)


def import_lights(document, zoom):
    """Read active-scene light instances independently of Assimp's optional light postprocessing."""
    definitions = document.get("extensions", {}).get("KHR_lights_punctual", {}).get("lights", [])
    if not isinstance(definitions, list):
        raise TypeError("KHR_lights_punctual lights must be a list.")
    if not definitions:
        return ()
    nodes, scenes = document.get("nodes", []), document.get("scenes", [])
    scene_index = document.get("scene", 0)
    if type(scene_index) is not int or not 0 <= scene_index < len(scenes):
        raise ValueError("Invalid glTF scene index for lights.")
    Matrix = renpy.display.matrix.Matrix
    result = []
    visited = set()

    def visit(index, parent):
        if type(index) is not int or not 0 <= index < len(nodes) or index in visited:
            raise ValueError("Invalid, cyclic, or multiply-parented glTF light node.")
        visited.add(index)
        node = nodes[index]
        if not isinstance(node, dict):
            raise TypeError("A glTF node must be an object.")
        matrix = parent * node_matrix(node)
        binding = node.get("extensions", {}).get("KHR_lights_punctual")
        if binding is not None:
            if not isinstance(binding, dict):
                raise ValueError("A glTF light binding must be an object.")
            light_index = binding.get("light")
            if type(light_index) is not int or not 0 <= light_index < len(definitions):
                raise ValueError("Invalid glTF light index.")
            source = definitions[light_index]
            if not isinstance(source, dict):
                raise ValueError("A glTF light definition must be an object.")
            spot = source.get("spot", {})
            if not isinstance(spot, dict):
                raise ValueError("A glTF spotlight cone must be an object.")
            color = vector(source.get("color", (1, 1, 1)), "light color")
            if min(color) < 0 or max(color) > 1:
                raise ValueError("glTF light color must be between zero and one.")
            light = GLTFLight(source.get("type"), color=color, intensity=source.get("intensity", 1),
                range=source.get("range"), inner_cone_angle=spot.get("innerConeAngle", 0),
                outer_cone_angle=spot.get("outerConeAngle", math.pi / 4),
                name=source.get("name", node.get("name", f"light_{light_index}")))
            result.append((light, matrix))
        for child in node.get("children", []):
            visit(child, matrix)

    basis = Matrix.scale(zoom, -zoom, zoom)
    for root in scenes[scene_index].get("nodes", []):
        visit(root, basis)
    return tuple(result)


def collect_scene(root, width, height, imported=True):
    """Follow Render's child-offset-before-reverse order without changing any cached Render or drawing GL."""
    Render = renpy.display.render.Render
    Matrix = renpy.display.matrix.Matrix
    identity = renpy.display.render.IDENTITY
    matrix_view, matrix_projection, _matrix_model = renpy.gl2.gl2draw.MATRIX_KINDS
    meshes, lights = [], []

    def descend(r, model, view, projection, uniforms, properties):
        if not isinstance(r, Render):
            return
        local_properties = r.properties
        if local_properties and local_properties.get("gltf_scene", False):
            return
        reverse, kind = r.reverse, r.matrix_kind
        props = properties | local_properties if local_properties else properties
        if reverse is not None and kind == matrix_projection:
            props = props | {"gltf_camera_projection": True}
        values = uniforms
        if r.uniforms:
            values = dict(uniforms)
            for key, value in r.uniforms.items():
                if key in values and key in renpy.config.merge_uniforms:
                    values[key] = renpy.config.merge_uniforms[key](values[key], value)
                else:
                    values[key] = value
        if local_properties and (imported or not local_properties.get("gltf_imported_lights", False)):
            for light, local in local_properties.get("gltf_lights", ()):
                matrix = model * local
                position = matrix.transform(*light.position, components=3)
                direction = (matrix.transform(*light.direction, w=0, components=3)
                    if light.kind != "point" else light.direction)
                lights.append((light, vector(position, "light position"), unit(direction)))

        def transformed(x=0, y=0):
            m = model * Matrix.offset(x, y, 0) if x or y else model
            if reverse is None:
                return m, view, projection
            m = m * reverse
            if kind == matrix_projection:
                return identity, identity, projection * view * m
            if kind == matrix_view:
                return identity, view * m, projection
            return m, view, projection

        if isinstance(r.mesh, renpy.gl2.gl2mesh3.Mesh3):
            m, v, p = transformed()
            meshes.append((r, m, v, p, values, props))
            return
        # Models and their meshes usually share the same zero-offset transform.
        centered = None
        for child, x, y, _focus, _main in r.children:
            if x or y:
                m, v, p = transformed(x, y)
            else:
                if centered is None:
                    centered = transformed()
                m, v, p = centered
            descend(child, m, v, p, values, props)

    descend(root, identity, identity, Matrix.texture_projection(max(width, 1), max(height, 1)), {}, {})
    # A recursive closure is a cycle, which would keep every mesh record until a garbage collection.
    descend = None
    return meshes, lights


def peeled_scene(scene, meshes, layers, refractive):
    """Bounded front-to-back depth discovery, then back-to-front HDR shading against completed farther layers."""
    Render = renpy.display.render.Render
    width, height = scene.get_size()
    drawable = not (scene.properties or {}).get("gltf_pixel_resolution", False)
    cameras = {(matrix_values(record[2]), matrix_values(record[3])) for record in meshes}
    if len(cameras) > 1:
        raise ValueError("Depth peeling requires one shared camera view and projection.")
    standard = {"renpy.gltf.unlit", "renpy.gltf.pbr", "renpy.gltf.specular", "renpy.gltf.clearcoat",
        "renpy.gltf.sheen", "renpy.geometry", "renpy.texture", "renpy.alpha", "renpy.matrixcolor"}
    if any(record[5].get("transparent", False) and set(record[0].shaders or ()) - standard for record in meshes):
        raise ValueError("Depth peeling supports the built-in glTF material shaders and standard color/alpha transforms.")

    full_coverage = False
    for _, _, _, _, uniforms, properties in meshes:
        color = uniforms.get("u_renpy_matrixcolor")
        if properties.get("transparent", False) and color is not None and (color.wdx or color.wdy or color.wdz):
            if refractive and properties.get("gltf_transmission", False):
                raise ValueError("Peeled refraction cannot use a matrixcolor that derives alpha from RGB.")
            full_coverage = True

    def variant(mode, shader=None):
        result = Render(width, height)
        for child, x, y, focus, main in scene.children:
            # Every pass redraws the same displayables; only the opaque pass reports their focus.
            result.blit(child, (x, y), focus=focus and mode == "opaque", main=main)
        for name, value in (scene.uniforms or {}).items():
            result.add_uniform(name, value)
        for name, value in (scene.properties or {}).items():
            result.add_property(name, value)
        for inherited in scene.shaders or ():
            result.add_shader(inherited)
        result.add_property("gltf_draw_mode", mode)
        result.add_property("depth", True)
        result.add_property("gltf_target", {"hdr": mode != "peel", "depth_texture": True,
            "gltf_depth_required": True, "mipmap": refractive and mode == "opaque", "drawable_resolution": drawable})
        if shader is not None:
            result.add_shader(shader)
        return result

    opaque = variant("opaque")
    previous = opaque
    depths = []
    for i in range(layers):
        depth = variant("peel", "renpy.gltf.peel_depth")
        if full_coverage:
            depth.add_shader("renpy.gltf.peel_full_coverage")
        depth.add_property("gltf_pass", "transparency_depth")
        depth.add_uniform("u_gltf_peel_first", float(i == 0))
        depth.add_uniform("u_gltf_peel_previous", previous)
        depth.add_uniform("u_gltf_peel_opaque", opaque)
        for name in ("gltf_peel_previous", "gltf_peel_opaque"):
            depth.add_property("texture_scaling_" + name, (renpy.uguu.GL_NEAREST, renpy.uguu.GL_NEAREST))
        depths.append(depth)
        previous = depth
    accumulated = opaque
    for index, depth in enumerate(reversed(depths)):
        copied = Render(width, height)
        copied.blit(accumulated, (0, 0))
        copied.mesh = True
        copied.add_shader("renpy.gltf.depth_copy")
        copied.add_uniform("u_gltf_copy_depth", accumulated)
        copied.add_property("texture_scaling_gltf_copy_depth", (renpy.uguu.GL_NEAREST, renpy.uguu.GL_NEAREST))
        copied.add_property("gltf_pass", "transparency_copy")
        copied.add_property("hdr", True)
        copied.add_property("mipmap", refractive)
        # A scale of 0 isn't applied when part of the mesh is behind a perspective camera.
        copied.add_property("mesh_oversample", False)
        layer = variant("layer", "renpy.gltf.peel_color")
        layer.add_property("gltf_pass", "transparency_layer")
        layer.add_uniform("u_gltf_peel_selected", depth)
        layer.add_property("texture_scaling_gltf_peel_selected", (renpy.uguu.GL_NEAREST, renpy.uguu.GL_NEAREST))
        layer.add_uniform("u_gltf_background", accumulated)
        layer.add_uniform("u_gltf_background_depth", accumulated)
        current = Render(width, height)
        current.add_property("depth", True)
        current.add_property("gltf_target", {"hdr": True, "depth_texture": True,
            "gltf_depth_required": True, "mipmap": refractive and index != layers - 1, "drawable_resolution": drawable})
        current.blit(copied, (0, 0))
        current.blit(layer, (0, 0))
        accumulated = current
    return accumulated


def refraction_guard(child, margin):
    """Expand on the drawable pixel grid, keeping camera FOV, world coordinates and the visible coverage mask."""
    Render = renpy.display.render.Render
    width, height = child.get_size()
    draw = renpy.display.draw
    loader = draw.texture_loader
    px, py = draw.virt_to_draw.transform(width, height)
    px, py = min(loader.max_texture_width, max(1, round(px))), min(loader.max_texture_height, max(1, round(py)))
    mx = min(math.ceil(px * margin), (loader.max_texture_width - px) // 2)
    my = min(math.ceil(py * margin), (loader.max_texture_height - py) // 2)
    if not (mx or my):
        return child, None, (0, 0, 1, 1)
    scaled = Render(width, height)
    scaled.reverse = renpy.display.matrix.Matrix.scale(px / max(width, 1), py / max(height, 1), 1)
    scaled.forward = scaled.reverse.inverse()
    scaled.blit(child, (0, 0))
    expanded = Render(px + 2 * mx, py + 2 * my)
    expanded.blit(scaled, (mx, my))
    expanded.add_property("gltf_guard_margin", (mx * width / px, my * height / py))
    expanded.add_property("gltf_pixel_resolution", True)
    def viewport(source):
        if not isinstance(source, Render):
            return None
        result = Render(source.width, source.height)
        result.xclipping, result.yclipping = source.xclipping, source.yclipping
        if source.matrix_kind == renpy.gl2.gl2draw.MATRIX_KINDS[1] and source.reverse is not None:
            result.mesh = renpy.gl2.gl2mesh2.Mesh2.texture_rectangle(0, 0, source.width, source.height, 0, 0, 1, 1)
            return result
        result.reverse, result.forward, result.matrix_kind = source.reverse, source.forward, source.matrix_kind
        for item, x, y, _focus, _main in source.children:
            clipped = viewport(item)
            if clipped is not None:
                result.blit(clipped, (x, y))
        return result if result.children else None

    mask = viewport(child)
    # This closure is also recursive, and runs every frame.
    viewport = None
    if mask is None:
        mask = Render(width, height)
        mask.mesh = renpy.gl2.gl2mesh2.Mesh2.texture_rectangle(0, 0, width, height, 0, 0, 1, 1)
    mask.add_property("gltf_pass", "refraction_guard_mask")
    mask.add_property("gltf_target", {"hdr": False, "mipmap": False, "drawable_resolution": True})
    mask.add_shader("renpy.gltf.coverage")
    return expanded, mask, (mx / expanded.width, my / expanded.height, px / expanded.width, py / expanded.height)


def light_uniforms(lights, meters_per_unit):
    """Pack fixed-size arrays so every compiled shader sees the same validated light layout."""
    if len(lights) > MAX_LIGHTS:
        raise ValueError(f"GLTFScene supports at most {MAX_LIGHTS} lights, including its manual directional light.")
    positions, directions, colors, cones = [], [], [], []
    for light, position, direction in lights:
        kind = ("directional", "point", "spot").index(light.kind)
        positions.append(tuple(position) + (float(kind),))
        directions.append(tuple(direction) + (light.range or 0.0,))
        colors.append(tuple(channel * light.intensity for channel in light.color) + (0.0,))
        cones.append((math.cos(light.inner_cone_angle), math.cos(light.outer_cone_angle), 0.0, 0.0))
    for values in (positions, directions, colors, cones):
        values.extend([(0.0, 0.0, 0.0, 0.0)] * (MAX_LIGHTS - len(lights)))
    return {"u_gltf_light_count": len(lights), "u_gltf_light_positions": tuple(positions),
        "u_gltf_light_directions": tuple(directions), "u_gltf_light_colors": tuple(colors),
        "u_gltf_light_cones": tuple(cones), "u_gltf_meters_per_unit": meters_per_unit}


def dot(a, b):
    return sum(x * y for x, y in zip(a, b))


def cross(a, b):
    return (a[1]*b[2] - a[2]*b[1], a[2]*b[0] - a[0]*b[2], a[0]*b[1] - a[1]*b[0])


def look_at(position, direction, up=None):
    forward = unit(direction)
    if up is None:
        up = (0, 0, 1) if abs(forward[1]) > 0.99 else (0, -1, 0)
    right = unit(cross(forward, up))
    top = cross(right, forward)
    return renpy.display.matrix.Matrix([
        *right, -dot(right, position), *top, -dot(top, position),
        *(-i for i in forward), dot(forward, position), 0, 0, 0, 1])


def perspective(angle, near, far):
    focal = 1 / math.tan(angle)
    return renpy.display.matrix.Matrix([
        focal, 0, 0, 0, 0, focal, 0, 0,
        0, 0, -(far + near) / (far - near), -2 * far * near / (far - near), 0, 0, -1, 0])


def mesh_corners(record):
    bounds = record[0].mesh.bounds
    if bounds is None:
        return ()
    return tuple(record[1].transform(x, y, z, components=3)
        for x in (bounds[0], bounds[3]) for y in (bounds[1], bounds[4]) for z in (bounds[2], bounds[5]))


def cascade_frusta(meshes, count, distance, meters_per_unit):
    """Static camera intervals, overlapping at transitions; inverse projection also handles Ren'Py's baked-in eye offset."""
    cameras = {(matrix_values(record[2]), matrix_values(record[3])) for record in meshes}
    if len(cameras) != 1 or any(not record[5].get("gltf_camera_projection", False) for record in meshes):
        raise ValueError("Cascaded shadows require one shared camera view and projection.")
    _source, _world, view, projection, _uniforms, _properties = meshes[0]
    inverse = (projection * view).inverse()

    def unproject(x, y, z):
        point = inverse.transform(x, y, z, components=4)
        return tuple(v / point[3] for v in point[:3]) if abs(point[3]) > 1e-12 else None

    near_center = unproject(0, 0, -1)
    if near_center is None:
        raise ValueError("Cascaded shadows require a finite camera near plane.")
    perspective_camera = abs(projection.wdz) > 1e-8
    if perspective_camera:
        eye = inverse.transform(0, 0, 1, w=0, components=4)
        if abs(eye[3]) < 1e-12:
            raise ValueError("Could not recover the perspective camera position.")
        eye = tuple(v / eye[3] for v in eye[:3])
    else:
        eye = view.inverse().transform(0, 0, 0, components=3)
    forward = unit(tuple(a - b for a, b in zip(near_center, eye))) if perspective_camera else unit(
        inverse.transform(0, 0, 1, w=0, components=3))
    near = max(dot(tuple(a - b for a, b in zip(near_center, eye)), forward), 0)
    far_center = unproject(0, 0, 1)
    far = number(distance / meters_per_unit, "shadow distance in scene units", 0)
    if far_center is not None:
        far = min(far, dot(tuple(a - b for a, b in zip(far_center, eye)), forward))
    if far <= near:
        raise ValueError("Shadow distance must exceed the camera's near plane.")
    near = max(near, far * 1e-6)
    limits = [near] + [.5 * (near * (far / near) ** (i / count) + near + (far - near) * i / count)
        for i in range(1, count + 1)]
    corners = [unproject(x, y, -1) for x in (-1, 1) for y in (-1, 1)]
    if any(corner is None for corner in corners):
        raise ValueError("Cascaded shadows require bounded camera near-plane corners.")
    frusta = []
    for i in range(count):
        lower = limits[i] - (limits[i] - limits[i - 1]) * .1 if i else limits[i]
        points = []
        for depth in (lower, limits[i + 1]):
            for corner in corners:
                if perspective_camera:
                    ray = tuple(a - b for a, b in zip(corner, eye))
                    points.append(tuple(a + b * depth / dot(ray, forward) for a, b in zip(eye, ray)))
                else:
                    base_depth = dot(tuple(a - b for a, b in zip(corner, eye)), forward)
                    points.append(tuple(a + b * (depth - base_depth) for a, b in zip(corner, forward)))
        frusta.append(tuple(points))
    return eye, forward, limits, tuple(frusta)


def cascade_matrix(points, scene_points, direction, size, margin):
    """A fixed light basis and rounded sphere keep scale stable; snapping translation keeps the world texel grid stable."""
    Matrix = renpy.display.matrix.Matrix
    view = look_at((0, 0, 0), direction)
    center = tuple(sum(point[i] for point in points) / len(points) for i in range(3))
    radius = max(max(math.dist(center, point) for point in points), 1e-30)
    quantum = math.ldexp(1.0, math.frexp(radius)[1] - 11)
    radius = math.ceil(radius / quantum) * quantum
    radius /= 1 - 4 / size  # Two texels protect PCF and snapping at frustum edges.
    x, y, _z = view.transform(*center, components=3)
    texel = 2 * radius / size
    x, y = (math.floor(value / texel + .5) * texel for value in (x, y))
    depths = [view.transform(*point, components=3)[2] for point in scene_points]
    z0, z1 = min(depths) - margin, max(depths) + margin
    return Matrix([1/radius, 0, 0, -x/radius, 0, 1/radius, 0, -y/radius,
        0, 0, -2/(z1-z0), (z1+z0)/(z1-z0), 0, 0, 0, 1]) * view


def shadow_uniforms(meshes, lights, size, bias, meters_per_unit, enabled, transmission, cache=None, owner=None,
                    *, cascades=1, distance=50.0, plan=None):
    """Build one depth atlas: one tile per directional/spot light and six radial-depth tiles per point light."""
    identity = renpy.display.render.IDENTITY
    Render = renpy.display.render.Render
    Matrix = renpy.display.matrix.Matrix
    matrices = [identity] * MAX_LIGHTS
    data = [(-1.0, 1.0, 0.0, 0.0)] * MAX_LIGHTS
    cascade_matrices = [identity] * (MAX_LIGHTS * 3)
    cascade_eye, cascade_forward, limits, frusta = (0, 0, 0), (0, 0, -1), [0, distance / meters_per_unit], ()
    jobs = []
    active = [entry for entry in lights if entry[0].cast_shadows and entry[0].intensity and any(entry[0].color)]
    # Bounds only place shadow cameras, so skip the per-mesh corner transforms when no light needs one.
    points = [point for record in meshes for point in mesh_corners(record)] if enabled and active else []
    count = sum(cascades if light.kind == "directional" else
        1 if light.kind == "spot" and light.outer_cone_angle < math.pi / 2 - .0001 else 6
        for light, _position, _direction in active) if enabled and points else 0
    columns = max(math.ceil(math.sqrt(count)), 1)
    rows = max(math.ceil(count / columns), 1)
    loader = getattr(renpy.display.draw, "texture_loader", None)
    maximum_width = getattr(loader, "max_texture_width", 4096)
    maximum_height = getattr(loader, "max_texture_height", 4096)
    tile_size = min(size, maximum_width // columns, maximum_height // (rows * (2 if plan is not None else 1))) if count else 1
    if count and tile_size < 64:
        raise ValueError("The renderer cannot fit the scene's shadow atlas.")
    if enabled and points and cascades > 1 and any(light.kind == "directional" for light, _p, _d in active):
        cascade_eye, cascade_forward, limits, frusta = cascade_frusta(meshes, cascades, distance, meters_per_unit)
    if enabled and points:
        low = tuple(min(p[i] for p in points) for i in range(3))
        high = tuple(max(p[i] for p in points) for i in range(3))
        center = tuple((a + b) / 2 for a, b in zip(low, high))
        radius = max(math.dist(low, high) / 2, 1.0)
        for index, (light, position, direction) in enumerate(lights):
            if not light.cast_shadows or not light.intensity or not any(light.color):
                continue
            base = len(jobs)
            far = max(max(math.dist(position, p) for p in points) * 1.05, 0.1)
            if light.range is not None:
                far = min(far, light.range / meters_per_unit)
            near = max(far * 0.0001, min(far * 0.5, 0.000001))
            if light.kind == "directional":
                if frusta:
                    margin = max(radius * .02, .01)
                    for level, corners in enumerate(frusta):
                        matrix = cascade_matrix(corners, points, direction, tile_size, margin)
                        if level:
                            cascade_matrices[index * 3 + level - 1] = matrix
                        else:
                            matrices[index] = matrix
                        jobs.append((matrix, position, far, False))
                    data[index] = (float(base), far, 0.0, bias)
                    continue
                eye = tuple(center[i] - direction[i] * radius * 2 for i in range(3))
                view = look_at(eye, direction)
                projected = [view.transform(*p, components=3) for p in points]
                margin = max(radius * 0.02, 0.01)
                x0, y0, z0 = (min(p[i] for p in projected) - margin for i in range(3))
                x1, y1, z1 = (max(p[i] for p in projected) + margin for i in range(3))
                projection = Matrix([
                    2/(x1-x0), 0, 0, -(x1+x0)/(x1-x0),
                    0, 2/(y1-y0), 0, -(y1+y0)/(y1-y0),
                    0, 0, -2/(z1-z0), (z1+z0)/(z1-z0), 0, 0, 0, 1])
                matrix = projection * view
                matrices[index] = matrix
                jobs.append((matrix, position, far, False))
            elif light.kind == "spot" and light.outer_cone_angle < math.pi / 2 - 0.0001:
                matrix = perspective(light.outer_cone_angle, near, far) * look_at(position, direction)
                matrices[index] = matrix
                jobs.append((matrix, position, far, False))
            else:
                # A hemisphere cannot use a finite perspective projection, so wide spots share the point-light faces.
                projection = perspective(math.pi / 4, near, far)
                for forward, up in (
                    ((1, 0, 0), (0, -1, 0)), ((-1, 0, 0), (0, -1, 0)),
                    ((0, 1, 0), (0, 0, 1)), ((0, -1, 0), (0, 0, -1)),
                    ((0, 0, 1), (0, -1, 0)), ((0, 0, -1), (0, -1, 0)),
                ):
                    jobs.append((projection * look_at(position, forward, up), position, far, True))
            data[index] = (float(base), far, near if len(jobs) - base == 1 and light.kind == "spot" else 0.0, bias)

    output = {"u_gltf_shadow_matrices": tuple(matrices), "u_gltf_shadow_data": tuple(data),
        "u_gltf_shadow_atlas": (float(columns), float(rows), float(tile_size), float(bias)),
        "u_gltf_cascade_matrices": tuple(cascade_matrices), "u_gltf_cascade_count": float(cascades),
        "u_gltf_cascade_splits": tuple((limits[1:] + [limits[-1]] * 4)[:4]),
        "u_gltf_cascade_near": limits[0], "u_gltf_cascade_eye": cascade_eye, "u_gltf_cascade_forward": cascade_forward}
    if plan is not None:
        plan.update(jobs=jobs, columns=columns, rows=rows, size=tile_size)

    def target(width, height, category):
        rv = Render(width, height)
        rv.add_property("depth", True)
        rv.add_property("gltf_pass", category)
        rv.add_property("gltf_target", {"hdr": False, "depth_texture": True,
            "gltf_depth_required": True, "mipmap": False, "drawable_resolution": False})
        return rv

    casters = [record for record in meshes if "u_material_base_color_uv0" in record[4]
        and record[4].get("u_material_gltf_alphamode", 0) != 2 and record[5].get("gltf_cast_shadows", True)]

    def draw(target, records, job, viewport):
        matrix, position, far, radial = job
        for source, world, _view, _projection, uniforms, properties in records:
            shadow = Render(0, 0)
            shadow.mesh = source.mesh
            shadow.reverse, shadow.forward = world, world.inverse()
            shadow.add_shader("renpy.gltf.shadow")
            for name, value in uniforms.items():
                shadow.add_uniform(name, value)
            shadow.add_uniform("u_gltf_shadow_matrix", matrix)
            shadow.add_uniform("u_gltf_shadow_origin", tuple(position) + (far,))
            shadow.add_uniform("u_gltf_shadow_radial", float(radial))
            shadow.add_uniform("u_gltf_transmission_enabled", float(transmission))
            for name, value in properties.items():
                if name.startswith(("texture_wrap", "texture_scaling")):
                    shadow.add_property(name, value)
            shadow.add_property("gltf_shadow_viewport", viewport)
            shadow.add_property("frustum_cull", True)
            shadow.add_property("cull_face", None)
            target.blit(shadow, (0, 0))

    if cache is None or columns * rows * tile_size * tile_size * 8 > cache.limit or not jobs:
        atlas = target(columns * tile_size, rows * tile_size, "shadow")
        for tile, job in enumerate(jobs):
            viewport = ((tile % columns) * tile_size, (tile // columns) * tile_size, tile_size, tile_size)
            draw(atlas, casters, job, viewport)
        return atlas, output

    owner = cache.owner(owner)
    prepared = []
    signatures = []
    caster_keys = [(record, shadow_signature(record, transmission)) for record in casters]
    for job in jobs:
        matrix, position, far, radial = job
        visible = [(record, key) for record, key in caster_keys if record[0].mesh.is_visible(matrix * record[1])]
        records = [record for record, key in visible]
        values = tuple(key for record, key in visible)
        signature = ((matrix_values(matrix), tuple(position), far, radial, tile_size, transmission, values)
            if all(value is not None for value in values) else None)
        prepared.append(records)
        signatures.append(signature)
    atlas_signature = (columns, rows, tuple(signatures)) if None not in signatures else None
    cached = cache.get(owner, "atlas", atlas_signature)
    if cached is not None:
        return cached, output
    atlas = target(columns * tile_size, rows * tile_size, "shadow_resolve")
    for tile, (job, records, signature) in enumerate(zip(jobs, prepared, signatures)):
        face = cache.get(owner, tile, signature)
        if face is None:
            profiler = getattr(renpy.display.draw, "gltf_profile", None)
            if profiler is not None:
                profiler.record("shadow", 0, culled=len(casters) - len(records))
            face = target(tile_size, tile_size, "shadow")
            draw(face, records, job, (0, 0, tile_size, tile_size))
            face.add_property("gltf_shadow_store", (cache, (cache.epoch, owner, tile, signature)))
        quad = Render(tile_size, tile_size)
        quad.blit(face, (0, 0))
        quad.mesh = True
        quad.add_shader("renpy.gltf.shadow_copy")
        quad.add_uniform("u_gltf_shadow", face)
        quad.add_property("texture_wrap_gltf_shadow", (renpy.uguu.GL_CLAMP_TO_EDGE, renpy.uguu.GL_CLAMP_TO_EDGE))
        quad.add_property("texture_scaling_gltf_shadow", (renpy.uguu.GL_NEAREST, renpy.uguu.GL_NEAREST))
        atlas.blit(quad, ((tile % columns) * tile_size, (tile // columns) * tile_size))
    atlas.add_property("gltf_shadow_store", (cache, (cache.epoch, owner, "atlas", atlas_signature)))
    return atlas, output


def glass_shadows(meshes, plan, opaque, meters_per_unit, cache=None, owner=None):
    """Two nearest transmitting surfaces per light ray, sharing the opaque atlas's exact light projections."""
    Render = renpy.display.render.Render
    jobs, columns, rows, size = (plan[name] for name in ("jobs", "columns", "rows", "size"))
    width, height = columns * size, rows * size
    casters = [record for record in meshes if "u_material_base_color_uv0" in record[4]
        and record[5].get("gltf_cast_shadows", True) and
        (record[4].get("u_material_gltf_alphamode", 0) == 2 or record[4].get("u_material_transmission_factor", 0) > 0)]
    keys = tuple(shadow_signature(record, True, colored=True) for record in casters)
    signature = (tuple((matrix_values(m), tuple(p), f, r) for m, p, f, r in jobs), width, height,
        meters_per_unit, keys) if all(key is not None for key in keys) else None
    # Opaque geometry affects which glass layers survive even when its color is irrelevant.
    opaque_keys = tuple(shadow_signature(record, True) for record in meshes
        if "u_material_base_color_uv0" in record[4] and record[5].get("gltf_cast_shadows", True)
        and record[4].get("u_material_gltf_alphamode", 0) != 2)
    signature = (signature, opaque_keys) if signature is not None and all(key is not None for key in opaque_keys) else None
    plan["glass_signature"], plan["glass_casters"] = signature, casters
    reference = cache.owner(owner) if cache is not None else None
    if cache is not None:
        cached = cache.get(reference, "glass", signature)
        if cached is not None:
            return cached

    layers = []
    for _level in range(2):
        layers.append(glass_layer(casters, plan, opaque, layers[0] if layers else None, meters_per_unit))
    atlas = Render(width, height * 2)
    atlas.add_property("depth", True)
    atlas.add_property("gltf_pass", "shadow_glass_resolve")
    atlas.add_property("gltf_target", {"hdr": True, "depth_texture": True,
        "gltf_depth_required": True, "mipmap": False, "drawable_resolution": False})
    for level, layer in enumerate(layers):
        quad = Render(width, height)
        quad.blit(layer, (0, 0))
        quad.mesh = True
        quad.add_shader("renpy.gltf.depth_copy")
        quad.add_uniform("u_gltf_copy_depth", layer)
        atlas.blit(quad, (0, level * height))
    if cache is not None:
        atlas.add_property("gltf_target_store", (cache, (cache.epoch, reference, "glass", signature)))
    return atlas


def glass_layer(casters, plan, opaque, previous, meters_per_unit, *, data=False):
    """Draw the same nearest glass surfaces as either transmittance or refracted-ray data."""
    Render = renpy.display.render.Render
    jobs, columns, rows, size = (plan[name] for name in ("jobs", "columns", "rows", "size"))
    width, height = columns * size, rows * size
    layer = Render(width, height)
    layer.add_property("depth", True)
    layer.add_property("gltf_pass", "caustics" if data else "shadow_glass")
    layer.add_property("gltf_target", {"hdr": True, "depth_texture": True,
        "gltf_depth_required": True, "mipmap": False, "drawable_resolution": False})
    for tile, (matrix, position, far, radial) in enumerate(jobs):
        for source, world, _view, _projection, uniforms, properties in casters:
            if not source.mesh.is_visible(matrix * world):
                continue
            shadow = Render(0, 0)
            shadow.mesh = source.mesh
            shadow.reverse, shadow.forward = world, world.inverse()
            shadow.add_shader("renpy.gltf.shadow")
            shadow.add_shader("renpy.gltf.shadow_glass")
            if data:
                shadow.add_shader("renpy.gltf.caustic_data")
            for name, value in uniforms.items():
                shadow.add_uniform(name, value)
            shadow.add_uniform("u_gltf_shadow_matrix", matrix)
            shadow.add_uniform("u_gltf_shadow_origin", (*position, far))
            shadow.add_uniform("u_gltf_shadow_radial", float(radial))
            # Projected directional/spot rays point along the inverse projection's center ray.
            inverse = matrix.inverse()
            a = inverse.transform(0, 0, -1, components=4)
            b = inverse.transform(0, 0, 0, components=4)
            direction = unit(tuple(b[i] / b[3] - a[i] / a[3] for i in range(3)))
            shadow.add_uniform("u_gltf_shadow_direction", (*direction, float(any((matrix.wdx, matrix.wdy, matrix.wdz)))))
            shadow.add_uniform("u_gltf_glass_opaque", opaque)
            shadow.add_uniform("u_gltf_glass_previous", previous if previous is not None else opaque)
            shadow.add_uniform("u_gltf_glass_level", float(previous is not None))
            shadow.add_uniform("u_gltf_glass_resolution", (float(width), float(height)))
            shadow.add_uniform("u_gltf_meters_per_unit", meters_per_unit)
            for name, value in properties.items():
                if name.startswith(("texture_wrap", "texture_scaling")):
                    shadow.add_property(name, value)
            shadow.add_property("gltf_shadow_viewport", ((tile % columns) * size, (tile // columns) * size, size, size))
            shadow.add_property("frustum_cull", True)
            shadow.add_property("cull_face", None)
            gl = renpy.uguu
            shadow.add_property("blend_func", (gl.GL_FUNC_ADD, gl.GL_ONE, gl.GL_ZERO, gl.GL_FUNC_ADD, gl.GL_ONE, gl.GL_ZERO))
            layer.blit(shadow, (0, 0))
    return layer


def caustic_shadows(meshes, plan, opaque, glass, meters_per_unit, cache=None, owner=None):
    """A bounded light-space mesh moves transmitted flux to the first opaque receiver, subtracting its straight-ray reference."""
    Render = renpy.display.render.Render
    jobs, columns, rows, size = (plan[name] for name in ("jobs", "columns", "rows", "size"))
    width, height = columns * size, rows * size
    points = [point for record in meshes for point in mesh_corners(record)]
    extent = math.dist(tuple(min(p[i] for p in points) for i in range(3)),
        tuple(max(p[i] for p in points) for i in range(3))) if points else 1
    signature = (plan["glass_signature"], extent) if plan["glass_signature"] is not None else None
    reference = cache.owner(owner) if cache is not None else None
    if cache is not None:
        cached = cache.get(reference, "caustics", signature)
        if cached is not None:
            return cached
    data = glass_layer(plan["glass_casters"], plan, opaque, None, meters_per_unit, data=True)
    atlas = Render(width, height)
    atlas.add_property("gltf_pass", "caustics")
    atlas.add_property("gltf_target", {"hdr": True, "mipmap": False, "drawable_resolution": False})
    grid = renpy.gl2.gl2mesh2.Mesh2.texture_grid_mesh(65, 65, 0, 0, 1, 1, 0, 0, 1, 1)
    gl = renpy.uguu
    for tile, (matrix, position, far, radial) in enumerate(jobs):
        for warp in (False, True):
            model = Render(0, 0)
            model.mesh = grid
            model.add_shader("renpy.gltf.caustic_scatter")
            model.add_uniform("u_gltf_shadow_matrix", matrix)
            model.add_uniform("u_gltf_caustic_inverse", matrix.inverse())
            model.add_uniform("u_gltf_shadow_origin", (*position, far))
            model.add_uniform("u_gltf_shadow_radial", float(radial))
            model.add_uniform("u_gltf_caustic_tile", ((tile % columns) / columns, (tile // columns) / rows, 1 / columns, 1 / rows))
            model.add_uniform("u_gltf_caustic_extent", max(extent, .000001))
            model.add_uniform("u_gltf_caustic_size", float(size))
            model.add_uniform("u_gltf_caustic_warp", float(warp))
            model.add_uniform("u_gltf_meters_per_unit", meters_per_unit)
            model.add_uniform("u_gltf_shadow", opaque)
            model.add_uniform("u_gltf_glass_color", glass)
            model.add_uniform("u_gltf_glass_depth", glass)
            model.add_uniform("u_gltf_caustic_data", data)
            for texture in ("gltf_shadow", "gltf_glass_color", "gltf_glass_depth", "gltf_caustic_data"):
                model.add_property("texture_scaling_" + texture, (gl.GL_NEAREST, gl.GL_NEAREST))
            model.add_property("gltf_shadow_viewport", ((tile % columns) * size, (tile // columns) * size, size, size))
            model.add_property("blend_func", (gl.GL_FUNC_ADD, gl.GL_ONE, gl.GL_ONE, gl.GL_FUNC_ADD, gl.GL_ZERO, gl.GL_ONE))
            model.add_property("cull_face", None)
            model.add_property("depth", False)
            atlas.blit(model, (0, 0))
    if cache is not None:
        atlas.add_property("gltf_target_store", (cache, (cache.epoch, reference, "caustics", signature)))
    return atlas
