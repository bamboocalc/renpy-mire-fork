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

"""Runtime glTF hierarchy, deformation streams and clip sampling; saved displayables retain controls and clocks."""

import array
import base64
import bisect
import itertools
import json
import math
import posixpath
import struct
import sys
from urllib.parse import unquote, urlsplit

import renpy

SUPPORTED_EXTENSIONS = frozenset((
    "KHR_lights_punctual", "KHR_texture_transform", "KHR_materials_unlit",
    "KHR_materials_emissive_strength", "KHR_materials_transmission", "KHR_materials_volume", "KHR_materials_ior",
    "KHR_materials_specular", "KHR_materials_clearcoat", "KHR_materials_sheen",
))


def index(value, sequence, name):
    if type(value) is not int or not 0 <= value < len(sequence):
        raise ValueError(f"Invalid glTF {name} index: {value!r}.")
    return value


def resolve(key, records, kind):
    """Indices disambiguate unnamed objects and duplicate names without inventing unstable identifiers."""
    if type(key) is int:
        return index(key, records, kind)
    matches = [i for i, record in enumerate(records) if record.get("name", "") == key]
    if not isinstance(key, str) or not key or len(matches) != 1:
        raise ValueError(f"Unknown or ambiguous glTF {kind} {key!r}; use a unique name or numeric index.")
    return matches[0]


def camera_definitions(document):
    """Validate glTF camera scalars before native matrix construction; omitted perspective far planes stay infinite."""
    result = []
    for source in document.get("cameras", []):
        if not isinstance(source, dict) or not isinstance(source.get("name", ""), str):
            raise TypeError("Cameras must be objects with optional string names.")
        kind = source.get("type")
        if kind not in ("perspective", "orthographic") or not isinstance(source.get(kind), dict):
            raise ValueError("Camera type must select perspective or orthographic parameters.")
        values = source[kind]
        near = renpy.gl2.gltf.number(values.get("znear"), "camera znear", 0)
        far = values.get("zfar")
        if far is not None:
            far = renpy.gl2.gltf.number(far, "camera zfar", 0)
            if far <= near:
                raise ValueError("Camera zfar must exceed znear.")
        record = {"name": source.get("name", ""), "type": kind, "znear": near, "zfar": far}
        if kind == "perspective":
            yfov = renpy.gl2.gltf.number(values.get("yfov"), "camera yfov", 0, math.pi)
            if not near or not 0 < yfov < math.pi:
                raise ValueError("Perspective cameras need positive znear and yfov between zero and pi.")
            aspect = values.get("aspectRatio")
            if aspect is not None:
                aspect = renpy.gl2.gltf.number(aspect, "camera aspectRatio", 0)
                if not aspect:
                    raise ValueError("Camera aspectRatio must be positive.")
            record.update(yfov=yfov, aspect=aspect)
        else:
            xmag = renpy.gl2.gltf.number(values.get("xmag"), "camera xmag")
            ymag = renpy.gl2.gltf.number(values.get("ymag"), "camera ymag")
            if not xmag or not ymag or far is None:
                raise ValueError("Orthographic cameras need nonzero magnification and a finite far plane.")
            record.update(xmag=xmag, ymag=ymag, aspect=abs(xmag / ymag))
        result.append(record)
    return result


def camera_matrices(definition, world, width, height, zoom):
    """Create a Ren'Py view and pixel projection, preserving camera orientation but ignoring node scale."""
    gltf = renpy.gl2.gltf
    scale = gltf.number(abs(zoom), "camera model zoom", 0.000000001)
    position = world.transform(0, 0, 0, components=3)
    forward = gltf.unit(world.transform(0, 0, -1, w=0, components=3))
    up = gltf.unit(world.transform(0, 1, 0, w=0, components=3))
    right = gltf.unit(gltf.cross(up, forward))
    down = gltf.cross(right, forward)
    if definition["type"] == "orthographic":
        right = tuple(value * (1 if definition["xmag"] > 0 else -1) for value in right)
        down = tuple(value * (1 if definition["ymag"] > 0 else -1) for value in down)
    back = tuple(-value for value in forward)
    view = renpy.display.matrix.Matrix([*right, -gltf.dot(right, position),
        *down, -gltf.dot(down, position), *back, -gltf.dot(back, position), 0, 0, 0, 1])
    aspect = definition["aspect"] or width / height
    viewport_width, viewport_height = min(width, height * aspect), min(height, width / aspect)
    near = definition["znear"] * scale
    far = definition["zfar"] * scale if definition["zfar"] is not None else None
    if definition["type"] == "perspective":
        focal = 1 / math.tan(definition["yfov"] / 2)
        values = [viewport_width * focal / (2 * aspect), 0, -viewport_width / 2, 0,
            0, viewport_height * focal / 2, -viewport_height / 2, 0,
            0, 0, -(far + near) / (far - near) if far is not None else -1,
            -2 * far * near / (far - near) if far is not None else -2 * near, 0, 0, -1, 0]
    else:
        values = [viewport_width / (2 * abs(definition["xmag"]) * scale), 0, 0, viewport_width / 2,
            0, viewport_height / (2 * abs(definition["ymag"]) * scale), 0, viewport_height / 2,
            0, 0, -2 / (far - near), -(far + near) / (far - near), 0, 0, 0, 1]
    projection = renpy.display.matrix.Matrix([gltf.number(value, "camera projection") for value in values])
    return view, projection, (viewport_width, viewport_height)


def node_overrides(settings):
    """Snapshot validated values so a caller's mutable dictionaries cannot change a saved instance."""
    if settings is None:
        return ()
    if not isinstance(settings, dict):
        raise TypeError("node_overrides must be a dictionary.")
    result = []
    for key, values in settings.items():
        if type(key) not in (str, int) or not isinstance(values, dict):
            raise ValueError("Node overrides need a name/index and a dictionary of local transform settings.")
        unknown = set(values) - {"translation", "rotation", "scale", "matrix", "visible", "weights"}
        if unknown:
            raise ValueError(f"Unknown node settings: {unknown!r}.")
        values = dict(values)
        visible = values.pop("visible", True)
        if type(visible) is not bool:
            raise TypeError("Node visibility must be a bool.")
        if "weights" in values:
            values["weights"] = morph_weights(values["weights"])
        renpy.gl2.gltf.node_matrix(values)
        result.append((key, tuple((name, tuple(value)) for name, value in values.items()) + (("visible", visible),)))
    return tuple(result)


class Accessors:
    """Validated animation and deformation streams, including interleaved and sparse accessors."""

    def __init__(self, document, filename):
        self.document = document
        self.filename = filename
        self.buffers = {}
        self.accessors = {}

    def buffer(self, buffer_index):
        definitions = self.document.get("buffers", [])
        definition = definitions[index(buffer_index, definitions, "buffer")]
        if buffer_index not in self.buffers:
            uri = definition.get("uri")
            if uri is None:
                if buffer_index != 0:
                    raise ValueError("Only the first GLB buffer can omit its URI.")
                with renpy.loader.load(self.filename) as source:
                    header = source.read(12)
                    if len(header) != 12 or header[:4] != b"glTF":
                        raise ValueError("An animation buffer without a URI requires a GLB container.")
                    _magic, version, total = struct.unpack("<4sII", header)
                    if version != 2:
                        raise ValueError("Expected a glTF 2 GLB container.")
                    data = None
                    while source.tell() < total:
                        chunk = source.read(8)
                        if len(chunk) != 8:
                            raise ValueError("Truncated GLB animation chunk.")
                        length, kind = struct.unpack("<I4s", chunk)
                        if length % 4 or length > total - source.tell():
                            raise ValueError("Invalid GLB animation chunk length.")
                        if kind == b"BIN\0":
                            data = source.read(length)
                            if len(data) != length:
                                raise ValueError("Truncated GLB animation buffer.")
                            break
                        source.seek(length, 1)
                    if data is None:
                        raise ValueError("GLB animation buffer is missing its BIN chunk.")
            elif uri.startswith("data:"):
                head, separator, encoded = uri.partition(",")
                if not separator or not head.endswith(";base64"):
                    raise ValueError("Animation data URIs must use base64 encoding.")
                data = base64.b64decode(encoded, validate=True)
            else:
                parts = urlsplit(uri)
                path = unquote(parts.path)
                if parts.scheme or parts.netloc or parts.query or parts.fragment or "\\" in path or ":" in path:
                    raise ValueError("Animation buffers require relative local resource URIs.")
                if path.startswith("/"):
                    raise ValueError("Animation buffers require relative local resource URIs.")
                path = posixpath.normpath(posixpath.join(posixpath.dirname(self.filename), path))
                with renpy.loader.load(path) as source:
                    data = source.read()
            size = definition.get("byteLength")
            if type(size) is not int or size < 0 or len(data) < size:
                raise ValueError("Truncated glTF animation buffer.")
            self.buffers[buffer_index] = memoryview(data)[:size]
        return self.buffers[buffer_index]

    def rows(self, view_index, offset, count, component, width, stride=None):
        views = self.document.get("bufferViews", [])
        view = views[index(view_index, views, "bufferView")]
        data = self.buffer(view.get("buffer"))
        formats = {5120: "b", 5121: "B", 5122: "h", 5123: "H", 5125: "I", 5126: "f"}
        if component not in formats:
            raise ValueError("Unsupported animation accessor component type.")
        row = struct.Struct("<" + formats[component] * width)
        stride = row.size if stride is None else stride
        start, length = view.get("byteOffset", 0), view.get("byteLength")
        if (type(start) is not int or type(length) is not int or start < 0 or length < 0
                or type(offset) is not int or offset < 0 or type(stride) is not int or stride < row.size
                or offset + (count - 1) * stride + row.size > length or start + length > len(data)
                or (start + offset) % (row.size // width) or stride % (row.size // width)):
            raise ValueError("Animation accessor exceeds its buffer view.")
        if stride == row.size:
            return tuple(row.iter_unpack(data[start + offset:start + offset + count * stride]))
        return tuple(row.unpack_from(data, start + offset + i * stride) for i in range(count))

    def read(self, accessor_index, kind, rotation=False, geometry=False):
        key = (accessor_index, kind, rotation, geometry)
        if key in self.accessors:
            return self.accessors[key]
        accessors = self.document.get("accessors", [])
        source = accessors[index(accessor_index, accessors, "accessor")]
        count, component = source.get("count"), source.get("componentType")
        normalized = source.get("normalized", False)
        if source.get("type") != kind or type(count) is not int or not 0 < count <= 1000000:
            raise ValueError(f"Animation accessor must be a nonempty {kind} with at most one million elements.")
        if not geometry and component != 5126 and not (rotation and component in (5120, 5121, 5122, 5123) and normalized is True):
            raise ValueError("Animation accessors require float32 values or normalized integer rotations.")
        if normalized and component not in (5120, 5121, 5122, 5123):
            raise ValueError("Only byte and short accessors can be normalized.")
        if component == 5126 and normalized:
            raise ValueError("Float animation accessors cannot be normalized.")
        width = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4, "MAT4": 16}[kind]
        if "bufferView" in source:
            views = self.document.get("bufferViews", [])
            view = views[index(source["bufferView"], views, "bufferView")]
            rows = self.rows(source["bufferView"], source.get("byteOffset", 0), count, component,
                width, view.get("byteStride"))
        else:
            if source.get("byteOffset", 0):
                raise ValueError("An accessor without a buffer view cannot have a byte offset.")
            rows = ((0.0,) * width,) * count
        if "sparse" in source:
            sparse = source["sparse"]
            size = sparse.get("count")
            if type(size) is not int or not 0 < size <= count:
                raise ValueError("Invalid sparse animation accessor count.")
            indices, values = sparse["indices"], sparse["values"]
            if indices.get("componentType") not in (5121, 5123, 5125):
                raise ValueError("Sparse animation indices must be unsigned integers.")
            keys = self.rows(indices.get("bufferView"), indices.get("byteOffset", 0), size,
                indices["componentType"], 1)
            replacement = self.rows(values.get("bufferView"), values.get("byteOffset", 0), size, component, width)
            rows = list(rows)
            previous = -1
            for (sparse_key,), value in zip(keys, replacement):
                if not previous < sparse_key < count:
                    raise ValueError("Sparse animation indices must increase and fit the accessor.")
                rows[sparse_key], previous = value, sparse_key
        if normalized:
            divisor = {5120: 127, 5121: 255, 5122: 32767, 5123: 65535}[component]
            rows = [tuple(max(value / divisor, -1.0) for value in row) for row in rows]
        elif component != 5126:
            rows = [tuple(map(float, row)) for row in rows]
        # Only float32 components can hold infinities or NaN.
        if component == 5126 and not all(map(math.isfinite, itertools.chain.from_iterable(rows))):
            raise ValueError("Animation samples must be finite.")
        result = tuple(rows)
        self.accessors[key] = result
        return result

    def geometry(self, accessor_index, kind, components=(5126,), normalized=False):
        """Keep the component rules at the semantic boundary, before native buffers receive any values."""
        sources = self.document.get("accessors", [])
        source = sources[index(accessor_index, sources, "geometry accessor")]
        if source.get("componentType") not in components or source.get("normalized", False) is not normalized:
            raise ValueError("Invalid deformation accessor component type or normalization.")
        return self.read(accessor_index, kind, geometry=True)


def morph_weights(value, count=None):
    if not isinstance(value, (list, tuple)) or (count is not None and len(value) != count):
        raise ValueError("Morph weights must match the mesh's target count.")
    return tuple(renpy.gl2.gltf.number(i, "morph weight") for i in value)


def material_import_document(document, filename):
    """Feed Assimp an in-memory static copy; glTF permits duplicate joint names that its bone validator rejects."""
    result = dict(document)
    result.pop("skins", None)
    result.pop("animations", None)
    result["nodes"] = [{key: value for key, value in node.items() if key not in ("skin", "weights")}
        for node in document.get("nodes", [])]
    result["meshes"] = []
    for mesh in document.get("meshes", []):
        copy = {key: value for key, value in mesh.items() if key not in ("weights", "primitives")}
        copy["primitives"] = [{**{key: value for key, value in primitive.items() if key not in ("targets", "attributes")},
            "attributes": {key: value for key, value in primitive.get("attributes", {}).items()
                if not key.startswith(("JOINTS_", "WEIGHTS_"))}} for primitive in mesh.get("primitives", [])]
        result["meshes"].append(copy)
    result["buffers"] = [dict(buffer) for buffer in document.get("buffers", [])]
    for i, buffer in enumerate(result["buffers"]):
        if "uri" not in buffer:
            data = Accessors(document, filename).buffer(i)
            buffer["uri"] = "data:application/octet-stream;base64," + base64.b64encode(data).decode("ascii")
    return json.dumps(result, separators=(",", ":")).encode("utf-8")


def blend_clip(value):
    """Snapshot a secondary clip and its blend amount as saved immutable controls."""
    if value is None:
        return None
    if not isinstance(value, (list, tuple)) or len(value) != 2 or type(value[0]) not in (str, int):
        raise ValueError("animation_blend must be a (clip name/index, weight) pair.")
    return value[0], renpy.gl2.gltf.number(value[1], "animation blend weight", 0, 1)


class DeformPrimitive:
    """Read authored vertex identities directly: native optimization can merge vertices with different morph deltas."""

    def __init__(self, primitive, reader):
        attributes = primitive.get("attributes", {})
        positions = reader.geometry(attributes.get("POSITION"), "VEC3")
        self.count = len(positions)
        if "indices" in primitive:
            indices = [int(row[0]) for row in reader.geometry(primitive["indices"], "SCALAR", (5121, 5123, 5125))]
        else:
            indices = list(range(self.count))
        if any(i >= self.count for i in indices):
            raise ValueError("Deformation triangle index exceeds its vertex count.")
        mode = primitive.get("mode", 4)
        if mode == 4 and len(indices) % 3 == 0:
            triangles = list(zip(indices[::3], indices[1::3], indices[2::3]))
        elif mode == 5:
            triangles = [(indices[i + i % 2], indices[i + 1 - i % 2], indices[i + 2]) for i in range(len(indices) - 2)]
        elif mode == 6:
            triangles = [(indices[0], indices[i], indices[i + 1]) for i in range(1, len(indices) - 1)]
        else:
            raise ValueError("Deforming meshes require triangle lists, strips, or fans.")
        if not triangles:
            raise ValueError("Deforming mesh contains no triangles.")

        def stream(name, kind, default, integer=False):
            if name not in attributes:
                return (default,) * self.count
            accessor = attributes[name]
            component = reader.document["accessors"][index(accessor, reader.document["accessors"], name)]["componentType"]
            values = reader.geometry(accessor, kind, (5121, 5123, 5126) if integer else (5126,),
                integer and component != 5126)
            if len(values) != self.count:
                raise ValueError("Deformation vertex streams must have identical counts.")
            return values

        normals = stream("NORMAL", "VEC3", (0, 0, 0))
        if "NORMAL" not in attributes:
            normals = [[0.0, 0.0, 0.0] for _ in positions]
            for a, b, c in triangles:
                p = tuple(y - x for x, y in zip(positions[a], positions[b]))
                q = tuple(y - x for x, y in zip(positions[a], positions[c]))
                normal = renpy.gl2.gltf.cross(p, q)
                for vertex in (a, b, c):
                    normals[vertex] = [x + y for x, y in zip(normals[vertex], normal)]
            normals = [tuple(v / max(math.hypot(*n), 1e-20) for v in n) for n in normals]
        tangents = stream("TANGENT", "VEC4", (0, 0, 0, 1))
        uv0 = stream("TEXCOORD_0", "VEC2", (0, 0), True)
        uv1 = stream("TEXCOORD_1", "VEC2", (0, 0), True)
        color_index = attributes.get("COLOR_0")
        color_kind = reader.document["accessors"][color_index]["type"] if color_index is not None else "VEC4"
        if color_kind not in ("VEC3", "VEC4"):
            raise ValueError("Vertex color requires VEC3 or VEC4.")
        colors = stream("COLOR_0", color_kind, (1, 1, 1, 1), True)
        self.positions = array.array("f", itertools.chain.from_iterable(positions))
        self.attributes = array.array("f")
        self.handedness = array.array("f")
        for n, t, uv, second, color in zip(normals, tangents, uv0, uv1, colors):
            if abs(abs(t[3]) - 1) > 0.001:
                raise ValueError("Tangent handedness must be -1 or 1.")
            b = tuple(value * t[3] for value in renpy.gl2.gltf.cross(n, t[:3]))
            self.attributes.extend((*uv, *n, *t[:3], *b, *second, *color, *((1,) if len(color) == 3 else ())))
            self.handedness.append(t[3])
        self.uv_sets = frozenset(i for i in (0, 1) if "TEXCOORD_" + str(i) in attributes)
        self.triangles = array.array("I", (i for a, b, c in triangles for i in (a, c, b)))
        self.material = primitive.get("material", len(reader.document.get("materials", [])))
        targets = primitive.get("targets", [])
        self.target_count = len(targets)
        self.targets = array.array("f")
        if self.target_count * self.count * 9 > 64000000:
            raise ValueError("Deformation morph streams exceed the 256 MB per-primitive limit.")
        for target in targets:
            if set(target) - {"POSITION", "NORMAL", "TANGENT"}:
                raise ValueError("Only position, normal, and tangent morph targets are supported.")
            values = [reader.geometry(target[name], "VEC3") if name in target else ((0, 0, 0),) * self.count
                for name in ("POSITION", "NORMAL", "TANGENT")]
            if any(len(rows) != self.count for rows in values):
                raise ValueError("Morph streams must match the base vertex count.")
            if "TANGENT" in target and "TANGENT" not in attributes:
                raise ValueError("A tangent morph requires authored base tangents.")
            self.targets.extend(itertools.chain.from_iterable(itertools.chain.from_iterable(zip(*values))))
        self.joint_starts = array.array("I", [0])
        self.joint_indices = array.array("I")
        self.joint_weights = array.array("f")
        sets = sorted(int(name[7:]) for name in attributes if name.startswith("JOINTS_"))
        if sets != list(range(len(sets))) or any("WEIGHTS_" + str(i) not in attributes for i in sets):
            raise ValueError("Skin attribute sets must be contiguous joint/weight pairs.")
        if any(name.startswith("WEIGHTS_") and "JOINTS_" + name[8:] not in attributes for name in attributes):
            raise ValueError("Skin attribute sets must be joint/weight pairs.")
        streams = []
        self.max_joint_index = -1
        for i in sets:
            joints = reader.geometry(attributes["JOINTS_" + str(i)], "VEC4", (5121, 5123))
            self.max_joint_index = max(self.max_joint_index, max(int(j) for row in joints for j in row))
            weights = stream("WEIGHTS_" + str(i), "VEC4", (0, 0, 0, 0), True)
            if len(joints) != self.count or any(not 0 <= w <= 1 for row in weights for w in row):
                raise ValueError("Skin weights must be between zero and one and match the vertex count.")
            streams.append((joints, weights))
        for vertex in range(self.count):
            influences = [(int(j), w) for joints, weights in streams for j, w in zip(joints[vertex], weights[vertex]) if w]
            total = sum(w for j, w in influences)
            if sets and not total:
                raise ValueError("Every skinned vertex requires a nonzero joint weight.")
            self.joint_indices.extend(j for j, w in influences)
            self.joint_weights.extend(w / total for j, w in influences)
            self.joint_starts.append(len(self.joint_indices))


def sample(channel, time):
    """Evaluate STEP, linear/spherical, or glTF Hermite interpolation without advancing mutable playback state."""
    _node, path, mode, times, values = channel
    left = max(0, bisect.bisect_right(times, time) - 1)
    if mode == "CUBICSPLINE":
        a = values[left * 3 + 1]
    else:
        a = values[left]
    if left == len(times) - 1 or time <= times[0] or mode == "STEP":
        return a
    duration = times[left + 1] - times[left]
    t = (time - times[left]) / duration
    if mode == "CUBICSPLINE":
        b, outgoing, incoming = values[(left + 1) * 3 + 1], values[left * 3 + 2], values[(left + 1) * 3]
        value = tuple((2*t**3 - 3*t*t + 1)*v + (t**3 - 2*t*t + t)*duration*dv
            + (-2*t**3 + 3*t*t)*w + (t**3 - t*t)*duration*dw for v, dv, w, dw in zip(a, outgoing, b, incoming))
    else:
        b = values[left + 1]
        dot = sum(v * w for v, w in zip(a, b)) if path == "rotation" else 1.0
        if dot < 0:
            b, dot = tuple(-v for v in b), -dot
        if path == "rotation" and dot < 0.9995:
            angle = math.acos(max(-1.0, min(1.0, dot)))
            value = tuple((math.sin((1-t)*angle)*v + math.sin(t*angle)*w) / math.sin(angle) for v, w in zip(a, b))
        else:
            value = tuple(v*(1-t) + w*t for v, w in zip(a, b))
    if path == "rotation":
        length = math.hypot(*value)
        if length < 1e-12:
            raise ValueError("Animation interpolation produced a zero quaternion.")
        value = tuple(v / length for v in value)
    return value


class Asset:
    """Shared import data. Pose maps are independent; unchanged matrices can share immutable bind-pose data."""

    def __init__(self, document, filename):
        self.nodes = document.get("nodes", [])
        self.materials = document.get("materials", [])
        if not isinstance(self.nodes, list) or not isinstance(self.materials, list):
            raise TypeError("glTF nodes and materials must be arrays.")
        for record in self.nodes + self.materials + document.get("animations", []):
            if not isinstance(record, dict) or not isinstance(record.get("name", ""), str):
                raise TypeError("glTF nodes, materials, and clips require objects with string names.")
        if any("material" not in primitive for mesh in document.get("meshes", [])
               for primitive in mesh.get("primitives", [])):
            self.materials = self.materials + [{}]
        self.animations = []
        self.warnings = []
        missing = set(document.get("extensionsRequired", [])) - SUPPORTED_EXTENSIONS
        if missing:
            raise ValueError(f"Unsupported required glTF extensions: {', '.join(sorted(missing))}.")
        for extension in sorted(set(document.get("extensionsUsed", [])) - SUPPORTED_EXTENSIONS):
            self.warnings.append(f"Optional extension {extension} is not supported by the scene path.")
        scenes = document.get("scenes", [])
        active = scenes[index(document.get("scene", 0), scenes, "scene")]
        self.roots = tuple(active.get("nodes", []))
        self.parents = [None] * len(self.nodes)
        self.order = []
        visited = set()

        def visit(node_index, parent):
            index(node_index, self.nodes, "node")
            if node_index in visited:
                raise ValueError("Cyclic or multiply-parented glTF scene node.")
            visited.add(node_index)
            node = self.nodes[node_index]
            if not isinstance(node, dict) or not isinstance(node.get("name", ""), str):
                raise TypeError("glTF nodes require an object and a string name.")
            renpy.gl2.gltf.node_matrix(node)
            self.parents[node_index] = parent
            self.order.append(node_index)
            for child in node.get("children", []):
                visit(child, node_index)

        for root in self.roots:
            visit(root, None)
        self.active = frozenset(visited)
        self.local_matrices = {i: renpy.gl2.gltf.node_matrix(self.nodes[i]) for i in self.order}
        self._bind_pose = None
        self.cameras = camera_definitions(document)
        self.camera_nodes = tuple(i for i in self.order if "camera" in self.nodes[i])
        for i in self.camera_nodes:
            index(self.nodes[i]["camera"], self.cameras, "camera")
        self.light_nodes = tuple(i for i in self.order if "KHR_lights_punctual" in self.nodes[i].get("extensions", {}))
        reader = Accessors(document, filename)
        self.meshes = document.get("meshes", [])
        self.geometries, self.morph_defaults, self.skins = {}, {}, {}
        for node_index in self.order:
            node = self.nodes[node_index]
            if "mesh" not in node:
                if "skin" in node or "weights" in node:
                    raise ValueError("Skin and morph weights require a mesh node.")
                continue
            mesh_index = index(node["mesh"], self.meshes, "mesh")
            mesh = self.meshes[mesh_index]
            primitives = mesh.get("primitives", [])
            counts = {len(p.get("targets", [])) for p in primitives}
            if len(counts) != 1:
                raise ValueError("A mesh's primitives must have the same morph target count.")
            count = next(iter(counts))
            self.morph_defaults[node_index] = morph_weights(node.get("weights", mesh.get("weights", (0,) * count)), count)
            for primitive_index, primitive in enumerate(primitives):
                key = mesh_index, primitive_index
                if (key not in self.geometries and primitive.get("mode", 4) in (4, 5, 6)
                        and (count or ("skin" in node and any(name.startswith(("JOINTS_", "WEIGHTS_"))
                            for name in primitive.get("attributes", {}))))):
                    self.geometries[key] = DeformPrimitive(primitive, reader)
            if "skin" not in node:
                continue
            definitions = document.get("skins", [])
            skin_index = index(node["skin"], definitions, "skin")
            if skin_index in self.skins:
                continue
            definition = definitions[skin_index]
            joints = tuple(index(i, self.nodes, "skin joint") for i in definition.get("joints", []))
            if not joints or len(set(joints)) != len(joints) or not set(joints) <= self.active:
                raise ValueError("A skin needs unique joint nodes in the active scene.")
            ancestors = []
            for joint in joints:
                chain = []
                while joint is not None:
                    chain.append(joint)
                    joint = self.parents[joint]
                ancestors.append(chain)
            common = next((i for i in ancestors[0] if all(i in chain for chain in ancestors)), None)
            root = definition.get("skeleton", common)
            if root is not None:
                root = index(root, self.nodes, "skin skeleton")
            if root is None or any(root not in chain for chain in ancestors):
                raise ValueError("Skin joints need a common ancestor containing the skeleton root.")
            inverse = (renpy.display.render.IDENTITY,) * len(joints)
            if "inverseBindMatrices" in definition:
                rows = reader.geometry(definition["inverseBindMatrices"], "MAT4")
                if len(rows) != len(joints):
                    raise ValueError("Skin inverse bind matrices must match the joint count.")
                inverse = tuple(renpy.gl2.gltf.node_matrix({"matrix": row}) for row in rows)
            self.skins[skin_index] = {"name": definition.get("name", ""), "joints": joints, "root": root, "inverse": inverse}
        for node_index in self.order:
            node = self.nodes[node_index]
            if "skin" in node:
                joint_count = len(self.skins[node["skin"]]["joints"])
                for i in range(len(self.meshes[node["mesh"]]["primitives"])):
                    primitive = self.geometries.get((node["mesh"], i))
                    if primitive is not None and primitive.max_joint_index >= joint_count:
                        raise ValueError("Skin vertex joint index exceeds its skin's joint count.")
        for definition in document.get("animations", []):
            channels, used = [], set()
            for channel in definition.get("channels", []):
                target = channel.get("target", {})
                path = target.get("path")
                if path not in ("translation", "rotation", "scale", "weights"):
                    self.warnings.append(f"Animation {definition.get('name', '')!r}: target {path!r} is unsupported.")
                    continue
                node_index = index(target.get("node"), self.nodes, "animation node")
                if (node_index, path) in used or (path != "weights" and "matrix" in self.nodes[node_index]):
                    raise ValueError("Animation targets must be unique TRS node properties.")
                used.add((node_index, path))
                samplers = definition.get("samplers", [])
                sampler = samplers[index(channel.get("sampler"), samplers, "animation sampler")]
                mode = sampler.get("interpolation", "LINEAR")
                if mode not in ("STEP", "LINEAR", "CUBICSPLINE"):
                    raise ValueError(f"Unsupported animation interpolation: {mode!r}.")
                times = tuple(row[0] for row in reader.read(sampler.get("input"), "SCALAR"))
                if times[0] < 0 or any(a >= b for a, b in itertools.pairwise(times)):
                    raise ValueError("Animation timestamps must be nonnegative and strictly increasing.")
                values = reader.read(sampler.get("output"), "SCALAR" if path == "weights" else
                    "VEC4" if path == "rotation" else "VEC3", path == "rotation")
                if path == "weights":
                    count = len(self.morph_defaults.get(node_index, ()))
                    if not count or len(values) % count:
                        raise ValueError("A morph animation requires matching mesh target weights.")
                    values = tuple(tuple(row[0] for row in values[i:i + count]) for i in range(0, len(values), count))
                if len(values) != len(times) * (3 if mode == "CUBICSPLINE" else 1):
                    raise ValueError("Animation output count does not match its timestamps.")
                if mode == "CUBICSPLINE" and len(times) < 2:
                    raise ValueError("Cubic animation requires at least two keyframes.")
                if path == "rotation":
                    quaternions = values[1::3] if mode == "CUBICSPLINE" else values
                    component = document["accessors"][sampler["output"]]["componentType"]
                    tolerance = 0.02 if component in (5120, 5121) else 0.001
                    if any(abs(math.hypot(*q) - 1) > tolerance for q in quaternions):
                        raise ValueError("Animation rotation keys must be unit quaternions.")
                    values = list(values)
                    for key in range(1 if mode == "CUBICSPLINE" else 0, len(values), 3 if mode == "CUBICSPLINE" else 1):
                        length = math.hypot(*values[key])
                        values[key] = tuple(v / length for v in values[key])
                    values = tuple(values)
                channels.append((node_index, path, mode, times, values))
            self.animations.append({"name": definition.get("name", ""), "channels": tuple(channels),
                "duration": max((channel[3][-1] for channel in channels), default=0.0)})
        self.warnings = tuple(dict.fromkeys(self.warnings))

    def info(self):
        """Return independent, serializable diagnostics rather than exposing the shared import's dictionaries."""
        return {"nodes": [{"index": i, "name": node.get("name", ""), "parent": self.parents[i],
            "children": tuple(node.get("children", ())), "mesh": node.get("mesh"), "active": i in self.active,
            "camera": node.get("camera")} for i, node in enumerate(self.nodes)],
            "materials": [{"index": i, "name": material.get("name", "")} for i, material in enumerate(self.materials)],
            "animations": [{"index": i, "name": clip["name"], "duration": clip["duration"],
                "channels": len(clip["channels"])} for i, clip in enumerate(self.animations)],
            "cameras": [{**camera, "index": i, "nodes": tuple(node for node in self.camera_nodes
                if self.nodes[node]["camera"] == i)} for i, camera in enumerate(self.cameras)],
            "skins": [{"index": i, "name": skin["name"], "joints": skin["joints"], "root": skin["root"]}
                for i, skin in self.skins.items()], "warnings": self.warnings}

    def camera_node(self, camera=None, node=None):
        """Resolve camera definitions and optional instances explicitly instead of choosing an ambiguous node."""
        candidates = self.camera_nodes
        if camera is not None:
            camera = resolve(camera, self.cameras, "camera")
            candidates = tuple(i for i in candidates if self.nodes[i]["camera"] == camera)
        if node is not None:
            node = resolve(node, self.nodes, "camera node")
            candidates = tuple(i for i in candidates if i == node)
        if not candidates or (len(candidates) > 1 and (camera is not None or node is not None)):
            raise ValueError("Unknown or multiply-instanced camera; select its active node explicitly.")
        return candidates[0]

    def memory_size(self):
        """Estimate retained Python metadata for the model-cache budget, counting shared rows once."""
        seen = set()

        def size(value):
            if id(value) in seen:
                return 0
            seen.add(id(value))
            total = sys.getsizeof(value)
            if isinstance(value, dict):
                total += sum(size(k) + size(v) for k, v in value.items())
            elif isinstance(value, (tuple, list)):
                total += sum(size(v) for v in value)
            elif isinstance(value, DeformPrimitive):
                total += size(vars(value))
            return total

        return size(vars(self))

    def clip_changes(self, animation, time, loop):
        """Sample clips independently so blending missing channels can fall back to the node's bind values."""
        if animation is None:
            return {}
        clip = self.animations[resolve(animation, self.animations, "animation")]
        if not clip["channels"]:
            raise ValueError("This animation contains no supported channels.")
        duration = clip["duration"]
        time = time % duration if loop and duration else min(max(time, 0.0), duration)
        changes = {}
        for channel in clip["channels"]:
            changes.setdefault(channel[0], {})[channel[1]] = sample(channel, time)
        return changes

    def pose(self, overrides, animation, time, loop, zoom, blend=None, include_weights=False):
        if self._bind_pose is None or self._bind_pose[0] != zoom:
            bind = {}
            basis = renpy.display.matrix.Matrix.scale(zoom, -zoom, zoom)
            for i in self.order:
                bind[i] = bind.get(self.parents[i], basis) * self.local_matrices[i]
            self._bind_pose = zoom, bind
        bind = self._bind_pose[1]
        changes = self.clip_changes(animation, time, loop)
        if blend is not None:
            secondary, weight, secondary_time = blend
            other = self.clip_changes(secondary, secondary_time, loop)
            for i in changes.keys() | other.keys():
                primary, secondary = changes.get(i, {}), other.get(i, {})
                values = {}
                for path in primary.keys() | secondary.keys():
                    default = self.morph_defaults.get(i, ()) if path == "weights" else self.nodes[i].get(path,
                        {"translation": (0, 0, 0), "rotation": (0, 0, 0, 1), "scale": (1, 1, 1)}[path])
                    values[path] = sample((i, path, "LINEAR", (0, 1), (primary.get(path, default), secondary.get(path, default))), weight)
                changes[i] = values
        for key, values in overrides:
            node_index = resolve(key, self.nodes, "node")
            if node_index not in self.active:
                raise ValueError(f"Node {key!r} is outside the active glTF scene.")
            changes.setdefault(node_index, {}).update(values)
        matrices, visible, moved, weights = {}, {}, set(), {}
        basis = renpy.display.matrix.Matrix.scale(zoom, -zoom, zoom)
        for i in self.order:
            node = self.nodes[i]
            values = changes.get(i, {})
            parent = self.parents[i]
            if i in self.morph_defaults:
                weights[i] = morph_weights(values.get("weights", self.morph_defaults[i]), len(self.morph_defaults[i]))
            elif "weights" in values:
                raise ValueError("Morph weights require a mesh node.")
            visible[i] = values.get("visible", True) and visible.get(parent, True)
            if parent not in moved and not any(key not in ("visible", "weights") for key in values):
                matrices[i] = bind[i]
                continue
            moved.add(i)
            local = self.local_matrices[i]
            if any(key not in ("visible", "weights") for key in values):
                transform = {key: node[key] for key in ("translation", "rotation", "scale", "matrix") if key in node}
                if "matrix" in values:
                    transform = {"matrix": values["matrix"]}
                else:
                    transform.update((key, value) for key, value in values.items() if key not in ("visible", "weights"))
                local = renpy.gl2.gltf.node_matrix(transform)
            matrices[i] = matrices.get(parent, basis) * local
        return (matrices, visible, weights) if include_weights else (matrices, visible)
