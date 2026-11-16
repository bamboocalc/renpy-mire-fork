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


import array
import base64
import collections
import functools
import json
import math
import struct
import threading
import time
import weakref
from libc.math cimport fminf, fmaxf, isfinite, sqrt, fabs
from libc.string cimport memset, memcpy
cimport cython

from typing import Iterable, Literal

from renpy.pygame.iostream cimport open_io
from renpy.pygame.sdl cimport SDL_IOStream

from assimpapi cimport (
    aiProcessPreset_TargetRealtime_Quality,
    aiProcess_CalcTangentSpace,
    aiProcess_FlipUVs,
    aiProcess_FlipWindingOrder,
    aiProcess_RemoveRedundantMaterials,
    aiProcess_FindInvalidData,
    aiProcess_ValidateDataStructure,
    aiProcess_SplitLargeMeshes,

    aiScene,
    aiMesh,
    aiMatrix4x4,
    aiFace,
    aiNode,
    aiPrimitiveType_TRIANGLE,
    aiPrimitiveType_LINE,
    aiPrimitiveType_POINT,

    aiTexture,
    aiTextureType,
    aiGetMaterialTextureCount,
    aiGetMaterialTexture,
    aiTextureType_NONE,
    aiTextureType_DIFFUSE,
    aiTextureType_SPECULAR,
    aiTextureType_AMBIENT,
    aiTextureType_EMISSIVE,
    aiTextureType_HEIGHT,
    aiTextureType_NORMALS,
    aiTextureType_SHININESS,
    aiTextureType_OPACITY,
    aiTextureType_DISPLACEMENT,
    aiTextureType_LIGHTMAP,
    aiTextureType_REFLECTION,
    aiTextureType_BASE_COLOR,
    aiTextureType_NORMAL_CAMERA,
    aiTextureType_EMISSION_COLOR,
    aiTextureType_METALNESS,
    aiTextureType_DIFFUSE_ROUGHNESS,
    aiTextureType_AMBIENT_OCCLUSION,
    aiTextureType_UNKNOWN,
    aiTextureType_SHEEN,
    aiTextureType_CLEARCOAT,
    aiTextureType_TRANSMISSION,

    aiPTI_Float,
    aiPTI_Double,
    aiPTI_Integer,
    aiPTI_Buffer,

    aiReturn,
    aiString,
    aiMaterial,
    aiGetMaterialFloatArray,
    aiGetMaterialIntegerArray,
    aiGetMaterialString,
    aiReleaseImport,
    assimp_get_error,

)

cdef extern from "assimpio.h":
    const aiScene *assimpio_import(const char *filename, unsigned int flags) noexcept nogil

from typing import Callable, Iterable

import renpy

from renpy.display.displayable import Displayable
from renpy.display.matrix import Matrix
from renpy.display.render import IDENTITY, Render
from renpy.display.im import Data, unoptimized_texture, render_for_texture
from renpy.gl2.gl2mesh3 cimport Mesh3, Point3
from renpy.gl2.gl2model import GL2Model


# A map from texture types to the corresponding assimp constants.
TEXTURE_TYPES = {
    "u_tex_none": aiTextureType_NONE,
    "u_tex_diffuse": aiTextureType_DIFFUSE,
    "u_tex_specular": aiTextureType_SPECULAR,
    "u_tex_ambient": aiTextureType_AMBIENT,
    "u_tex_emissive": aiTextureType_EMISSIVE,
    "u_tex_height": aiTextureType_HEIGHT,
    "u_tex_normals": aiTextureType_NORMALS,
    "u_tex_shininess": aiTextureType_SHININESS,
    "u_tex_opacity": aiTextureType_OPACITY,
    "u_tex_displacement": aiTextureType_DISPLACEMENT,
    "u_tex_lightmap": aiTextureType_LIGHTMAP,
    "u_tex_reflection": aiTextureType_REFLECTION,
    "u_tex_base_color": aiTextureType_BASE_COLOR,
    "u_tex_normal_camera": aiTextureType_NORMAL_CAMERA,
    "u_tex_emission_color": aiTextureType_EMISSION_COLOR,
    "u_tex_metalness": aiTextureType_METALNESS,
    "u_tex_diffuse_roughness": aiTextureType_DIFFUSE_ROUGHNESS,
    "u_tex_ambient_occlusion": aiTextureType_AMBIENT_OCCLUSION,
    "u_tex_unknown": aiTextureType_UNKNOWN,
    "u_tex_sheen": aiTextureType_SHEEN,
    "u_tex_clearcoat": aiTextureType_CLEARCOAT,
    "u_tex_transmission": aiTextureType_TRANSMISSION,
}

# Standard glTF slots, their JSON locations, and their color-space interpretation.
MATERIAL_TEXTURES = {
    "u_tex_base_color": ("baseColorTexture", aiTextureType_BASE_COLOR, True, 0),
    "u_tex_metalness": ("metallicRoughnessTexture", aiTextureType_METALNESS, False, 0),
    "u_tex_normals": ("normalTexture", aiTextureType_NORMALS, False, 0),
    "u_tex_lightmap": ("occlusionTexture", aiTextureType_LIGHTMAP, False, 0),
    "u_tex_emissive": ("emissiveTexture", aiTextureType_EMISSIVE, True, 0),
    "u_tex_transmission": ("transmissionTexture", aiTextureType_TRANSMISSION, False, 0),
    "u_tex_thickness": ("thicknessTexture", aiTextureType_TRANSMISSION, False, 1),
    "u_tex_specular": ("specularTexture", -1, False, 0),
    "u_tex_specular_color": ("specularColorTexture", -1, True, 0),
    "u_tex_clearcoat": ("clearcoatTexture", -1, False, 0),
    "u_tex_clearcoat_roughness": ("clearcoatRoughnessTexture", -1, False, 0),
    "u_tex_clearcoat_normals": ("clearcoatNormalTexture", -1, False, 0),
    "u_tex_sheen_color": ("sheenColorTexture", -1, True, 0),
    "u_tex_sheen_roughness": ("sheenRoughnessTexture", -1, False, 0),
}

MATERIAL_TEXTURE_EXTENSIONS = {
    "transmissionTexture": "KHR_materials_transmission", "thicknessTexture": "KHR_materials_volume",
    "specularTexture": "KHR_materials_specular", "specularColorTexture": "KHR_materials_specular",
    "clearcoatTexture": "KHR_materials_clearcoat", "clearcoatRoughnessTexture": "KHR_materials_clearcoat",
    "clearcoatNormalTexture": "KHR_materials_clearcoat", "sheenColorTexture": "KHR_materials_sheen",
    "sheenRoughnessTexture": "KHR_materials_sheen",
}


def _material_document(filename):
    """Read only the JSON chunk of a GLB; geometry and images remain Assimp's responsibility."""
    with renpy.loader.load(filename) as f:
        header = f.read(12)
        if header[:4] == b"glTF":
            if len(header) != 12:
                raise ValueError(f"Truncated GLB header: {filename}")
            magic, version, size = struct.unpack("<4sII", header)
            chunk = f.read(8)
            if version != 2 or len(chunk) != 8:
                raise ValueError(f"Invalid GLB JSON header: {filename}")
            length, kind = struct.unpack("<I4s", chunk)
            if kind != b"JSON" or length > size - 20:
                raise ValueError(f"Invalid GLB JSON chunk: {filename}")
            data = f.read(length)
            if len(data) != length:
                raise ValueError(f"Truncated GLB JSON chunk: {filename}")
            document = json.loads(data)
        else:
            f.seek(0)
            document = json.load(f)
    if not isinstance(document, dict):
        raise ValueError(f"Expected a glTF object: {filename}")
    return document


def _material_vector(value, size, name):
    """Reject invalid material vectors before sending their components to native code or GLSL."""
    if not isinstance(value, (list, tuple)) or len(value) != size:
        raise ValueError(f"{name} must contain {size} numbers.")
    if any(type(v) not in (int, float) or not math.isfinite(v) for v in value):
        raise ValueError(f"{name} must contain finite numbers.")
    return tuple(value)


def _standard_overrides(values):
    """Use the same texture contract for whole-model and named-material overrides."""
    uniforms, textures = {}, {}
    for name, value in values.items():
        if not isinstance(name, str):
            raise ValueError("Material setting names must be strings.")
        if name.startswith("u_tex_"):
            image = renpy.easy.displayable(value)
            if not isinstance(image, renpy.display.im.ImageBase):
                raise ValueError("Standard material textures require image filenames or image manipulators.")
            textures[name] = renpy.display.im.UnoptimizedTexture(image, premultiply=False,
                srgb=MATERIAL_TEXTURES.get(name, (None, None, False))[2])
            if name in MATERIAL_TEXTURES:
                uniforms.setdefault("u_material_" + name[6:] + "_present", 1.0)
        elif name.startswith("u_"):
            uniforms[name] = tuple(value) if isinstance(value, (list, tuple)) else value
        else:
            raise ValueError(f"Unknown material setting {name!r}. Uniforms must start with 'u_'.")
    return uniforms, textures


def free_memory():
    with loader_lock:
        with cache_lock:
            cache.clear()


def get_diagnostics():
    """Snapshots native storage and estimated scene metadata, excluding decoded images and GPU buffers."""

    predicted_keys = { i.cache_key for i in predicted } if predicted is not None else set()
    with cache_lock:
        models = [
            { "filename": key[0], "bytes": data.memory_size, "predicted": key in predicted_keys,
              "scene_metadata_bytes": getattr(data, "scene_memory_size", 0),
              "deformation_bytes": data.deformation_bytes(),
              "meshes": len(data.mesh_info), "draws": len(data.blit_info),
              "triangles": sum(mi.mesh.triangles for mi in data.mesh_info) }
            for key, data in cache.items()
        ]
    return { "models": models, "native_bytes": sum(m["bytes"] - m["scene_metadata_bytes"] for m in models),
        "scene_metadata_bytes": sum(m["scene_metadata_bytes"] for m in models),
        "inactive_bytes": sum(m["bytes"] for m in models if not m["predicted"]) }


class MeshInfo:
    """
    This stores the information used to blit a texture.
    """

    deformation = None

    mesh: Mesh3
    "The mesh that's being loaded."

    uniforms: dict[str, object]
    """
    A dictionary of uniforms that are set by the material.
    """

    texture_uniforms: dict[str, Displayable]
    """
    A dictionary of texture uniforms that are set by the material.
    """

    twosided: bool
    """
    True of the mesh is two-sided, and should be rendered with backface culling disabled.
    """

    def __init__(self, Loader loader, Mesh3 mesh, int material_index, textures: Iterable[str]):

        self.mesh = mesh
        self.material_index = material_index

        self.uniforms = loader.get_material_uniforms(material_index)
        self.properties = { }
        if loader.material:
            self.properties["gltf_winding"] = True
        self.texture_uniforms = loader.get_texture_uniforms(material_index, self.uniforms, self.properties)
        self.twosided = loader.get_twosided(material_index)


class BlitInfo:
    """
    This stores the information used to blit a texture.
    """

    def __init__(self, reverse: Matrix, forward: Matrix, mesh_info: MeshInfo, node_index=-1):
        """
        Initializes the BlitInfo.
        """

        self.reverse: Matrix = reverse
        self.forward: Matrix = forward
        self.mesh_info: MeshInfo = mesh_info
        self.node_index = node_index

        if self.mesh_info is None:
            raise ValueError("MeshInfo cannot be None.")

class ModelData:
    """
    Represents the information about a model after it's been loaded.
    """

    filename: str
    "The filename of the model."

    embedded_textures: dict[str, Data]
    "The embedded textures in the model."

    blit_info: list[BlitInfo]
    "Information required to blit each mesh in the model."

    mesh_info: list[MeshInfo]
    "A list of MeshInfo objects used by the blit_info objects."

    memory_size: int
    "Native buffers, compressed embedded images, and estimated scene metadata; excludes image-cache textures."

    def __init__(self):
        self.memory_size = 0
        self.embedded_textures = { }
        self.blit_info = [ ]
        self.lights = ()
        self.asset = None
        self.scene_memory_size = 0
        self.prepared = weakref.WeakKeyDictionary()
        self.deformed = weakref.WeakKeyDictionary()

    def deformation_bytes(self):
        """Latest per-instance poses belong to the same import-cache budget and disappear with weak owners."""
        return sum(mesh.points * (3 + mesh.layout.stride) * 4 + mesh.triangles * 12
            for entries in self.deformed.values() for signature, mesh in entries.values())

    @property
    def memory_size(self):
        return self._memory_size + self.deformation_bytes()

    @memory_size.setter
    def memory_size(self, value):
        self._memory_size = value


    def report(self):
        """
        Prints a report of the model data.
        """

        def log(msg: str):
            """
            Prints a message to the log.
            """

            renpy.display.log.write("%s", msg)

        log("")
        log(f"GLTFModel {self.filename!r}")
        log(f"  Light instances: {len(self.lights)}")
        if self.asset is not None:
            log(f"  Scene nodes: {len(self.asset.order)}, animation clips: {len(self.asset.animations)}")
            log(f"  Estimated scene metadata: {self.scene_memory_size} bytes")
            info = self.asset.info()
            for node in info["nodes"]:
                if node["active"]:
                    log(f"    Node {node['index']}: {node['name']!r}, parent={node['parent']}, mesh={node['mesh']}")
            for material in info["materials"]:
                log(f"    Material {material['index']}: {material['name']!r}")
            for clip in info["animations"]:
                log(f"    Animation {clip['index']}: {clip['name']!r}, {clip['duration']:g} seconds, {clip['channels']} channels")
            for warning in self.asset.warnings:
                log(f"  Warning: {warning}")
        if not self.mesh_info:
            return

        minx = miny = minz = float("inf")
        maxx = maxy = maxz = float("-inf")

        for bi in self.blit_info:
            for x, y, z, w in bi.mesh_info.mesh.get_points():

                x, y, z = bi.reverse.transform(x, y, z, components=3)

                minx = min(minx, x)
                miny = min(miny, y)
                minz = min(minz, z)

                maxx = max(maxx, x)
                maxy = max(maxy, y)
                maxz = max(maxz, z)

        log(f"  Bounding box (after zoom):")

        log(f"    X: {minx:.3f} to {maxx:.3f}")
        log(f"    Y: {miny:.3f} to {maxy:.3f}")
        log(f"    Z: {minz:.3f} to {maxz:.3f}")

        uniform_sets = [ ]

        min_values = { }
        max_values = { }

        types: dict[str, str] = {}

        def recursive_min(a, b):
            """
            Returns the minimum of a and b. If a and b are tuples, it returns a tuple of the minimums.
            """

            if isinstance(a, tuple) and isinstance(b, tuple):
                return tuple(recursive_min(x, y) for x, y in zip(a, b))
            else:
                return min(a, b)

        def recursive_max(a, b):
            """
            Returns the maximum of a and b. If a and b are tuples, it returns a tuple of the maximums.
            """
            if isinstance(a, tuple) and isinstance(b, tuple):
                return tuple(recursive_max(x, y) for x, y in zip(a, b))
            else:
                return max(a, b)


        def recursive_format(v):
            """
            Formats a value for display. If it's a tuple, it formats each element.
            """

            if v is None:
                return "N/A"

            if isinstance(v, tuple):
                return "(" + ", ".join(f"{x:.5g}" for x in v) + ")"
            else:
                return f"{v:.5g}"

        for i in self.mesh_info:
            uniform_sets.append(set(i.uniforms) | set(i.texture_uniforms))

            for k, v in i.uniforms.items():
                if k not in min_values:
                    min_values[k] = v
                    max_values[k] = v
                else:
                    min_values[k] = recursive_min(min_values[k], v)
                    max_values[k] = recursive_max(max_values[k], v)

                if isinstance(v, float):
                    types[k] = "float"
                elif isinstance(v, int):
                    types[k] = "int"
                elif isinstance(v, tuple):
                    types[k] = f"vec{len(v)}"
                else:
                    types[k] = "unknown"

            for k, v in i.texture_uniforms.items():
                types[k] = "sampler2D"


        any_uniforms = set.union(*uniform_sets)
        all_uniforms = set.intersection(*uniform_sets)
        all_uniforms.add("u_tex_diffuse")

        log("  Uniforms:")

        for u in sorted(any_uniforms):

            min_value = min_values.get(u, None)
            max_value = max_values.get(u, None)

            comments = [ ]

            if min_value is not None:
                comments.append(f"range {recursive_format(min_value)} to {recursive_format(max_value)}")

            if u not in all_uniforms:
                comments.append("not in all meshes")

            uniform_type = types.get(u, "unknown")

            comment = "; ".join(comments)
            if comment:
                comment = " // " + comment


            log(f"    uniform {uniform_type} {u};{comment}")


cache: collections.OrderedDict[tuple, ModelData] = collections.OrderedDict()
"Imported models by GLTFModel.cache_key, ordered from least to most recently loaded or rendered."

cache_lock = threading.Lock()
"Protects cache membership and recency without waiting for native imports."

predicted: set[GLTFModel]|None = None
"The set of models that are predicted to be loaded."

new_predicted: set[GLTFModel] = set()
"The same, but before finish_predict() is called."

@cython.boundscheck(False)
@cython.wraparound(False)
def _deform_geometry(source, weights, matrices):
    """Morph before linear-blend skinning; publish a fresh immutable mesh for every changed pose."""
    if len(weights) != source.target_count:
        raise ValueError("Morph weights must match the target count.")
    cdef const float[::1] positions = source.positions
    cdef const float[::1] attributes = source.attributes
    cdef const float[::1] targets = source.targets
    cdef const float[::1] signs = source.handedness
    cdef const unsigned int[::1] starts = source.joint_starts
    cdef const unsigned int[::1] joints = source.joint_indices
    cdef const float[::1] influences = source.joint_weights
    cdef const float[::1] morph = array.array("f", weights)
    cdef const float[::1] bones = array.array("f", (v for matrix in matrices for v in renpy.gl2.gltf.matrix_values(matrix)))
    cdef bint skin = bool(matrices)
    if skin and joints.shape[0] and max(source.joint_indices) >= len(matrices):
        raise ValueError("Vertex joint index exceeds the skin's joint count.")
    cdef int count = source.count, stride = renpy.gl2.gl2mesh.GLTF_T_LAYOUT.stride
    cdef int target_count = source.target_count, vertex, target, component, offset, at, bone
    cdef unsigned int influence
    cdef double values[9]
    cdef double transform[12]
    cdef double normal[3]
    cdef double tangent[3]
    cdef double x, y, z, weight, determinant, length, projection, handedness
    cdef float minx = float("inf"), miny = minx, minz = minx
    cdef float maxx = -minx, maxy = maxx, maxz = maxx
    cdef Mesh3 mesh = Mesh3(renpy.gl2.gl2mesh.GLTF_T_LAYOUT, count, len(source.triangles) // 3)
    mesh.points, mesh.triangles = count, len(source.triangles) // 3
    mesh.set_triangle_data(source.triangles)
    with nogil:
        memcpy(mesh.attribute, &attributes[0], count * stride * sizeof(float))
        for vertex in range(count):
            for component in range(3):
                values[component] = positions[vertex * 3 + component]
                values[component + 3] = attributes[vertex * stride + 2 + component]
                values[component + 6] = attributes[vertex * stride + 5 + component]
            for target in range(target_count):
                weight = morph[target]
                if weight == 0:
                    continue
                offset = (target * count + vertex) * 9
                for component in range(9):
                    values[component] += weight * targets[offset + component]
            determinant = 1.0
            if skin:
                for component in range(12):
                    transform[component] = 0
                for influence in range(starts[vertex], starts[vertex + 1]):
                    bone = joints[influence] * 16
                    weight = influences[influence]
                    for component in range(12):
                        transform[component] += weight * bones[bone + component]
                x = transform[0] * values[0] + transform[1] * values[1] + transform[2] * values[2] + transform[3]
                y = transform[4] * values[0] + transform[5] * values[1] + transform[6] * values[2] + transform[7]
                z = transform[8] * values[0] + transform[9] * values[1] + transform[10] * values[2] + transform[11]
                normal[0] = transform[5]*transform[10] - transform[6]*transform[9]
                normal[1] = transform[6]*transform[8] - transform[4]*transform[10]
                normal[2] = transform[4]*transform[9] - transform[5]*transform[8]
                determinant = transform[0]*normal[0] + transform[1]*normal[1] + transform[2]*normal[2]
                normal[0] = normal[0]*values[3] + normal[1]*values[4] + normal[2]*values[5]
                normal[1] = (transform[2]*transform[9] - transform[1]*transform[10])*values[3] + (transform[0]*transform[10] - transform[2]*transform[8])*values[4] + (transform[1]*transform[8] - transform[0]*transform[9])*values[5]
                normal[2] = (transform[1]*transform[6] - transform[2]*transform[5])*values[3] + (transform[2]*transform[4] - transform[0]*transform[6])*values[4] + (transform[0]*transform[5] - transform[1]*transform[4])*values[5]
                if determinant < 0:
                    for component in range(3):
                        normal[component] = -normal[component]
                for component in range(3):
                    at = component * 4
                    tangent[component] = transform[at]*values[6] + transform[at + 1]*values[7] + transform[at + 2]*values[8]
            else:
                x, y, z = values[0], values[1], values[2]
                for component in range(3):
                    normal[component], tangent[component] = values[component + 3], values[component + 6]
            if not (isfinite(x) and isfinite(y) and isfinite(z)) or fabs(x) > 3e38 or fabs(y) > 3e38 or fabs(z) > 3e38:
                raise ValueError("Deformation produced a non-finite float32 position.")
            mesh.point[vertex].x, mesh.point[vertex].y, mesh.point[vertex].z = x, y, z
            minx, miny, minz = fminf(minx, x), fminf(miny, y), fminf(minz, z)
            maxx, maxy, maxz = fmaxf(maxx, x), fmaxf(maxy, y), fmaxf(maxz, z)
            length = sqrt(normal[0]*normal[0] + normal[1]*normal[1] + normal[2]*normal[2])
            if length < 1e-20:
                normal[0], normal[1], normal[2], length = 0, 0, 1, 1
            for component in range(3):
                normal[component] /= length
            projection = normal[0]*tangent[0] + normal[1]*tangent[1] + normal[2]*tangent[2]
            for component in range(3):
                tangent[component] -= projection * normal[component]
            length = sqrt(tangent[0]*tangent[0] + tangent[1]*tangent[1] + tangent[2]*tangent[2])
            if length < 1e-20:
                length = 1
            for component in range(3):
                tangent[component] /= length
                mesh.attribute[vertex * stride + 2 + component] = normal[component]
                mesh.attribute[vertex * stride + 5 + component] = tangent[component]
            handedness = signs[vertex] * (-1.0 if determinant < 0 else 1.0)
            mesh.attribute[vertex * stride + 8] = (normal[1]*tangent[2] - normal[2]*tangent[1]) * handedness
            mesh.attribute[vertex * stride + 9] = (normal[2]*tangent[0] - normal[0]*tangent[2]) * handedness
            mesh.attribute[vertex * stride + 10] = (normal[0]*tangent[1] - normal[1]*tangent[0]) * handedness
    mesh.bounds = (minx, miny, minz, maxx, maxy, maxz)
    mesh.point_version = mesh.attribute_version = 1
    return mesh


@cython.boundscheck(False)
@cython.wraparound(False)
def _pack_deform_vertices(Py_ssize_t count, const double[::1] normals, const float[::1] tangents,
        const float[::1] uv0, const float[::1] uv1, const float[::1] colors, int color_width):
    """Interleave GLTF_T_LAYOUT attributes and tangent signs; bitangents use double precision, as before."""
    cdef Py_ssize_t stride = renpy.gl2.gl2mesh.GLTF_T_LAYOUT.stride, vertex, o
    cdef double nx, ny, nz, tx, ty, tz, sign
    if (stride != 17 or color_width not in (3, 4) or normals.shape[0] != count * 3 or tangents.shape[0] != count * 4
            or uv0.shape[0] != count * 2 or uv1.shape[0] != count * 2 or colors.shape[0] != count * color_width):
        raise ValueError("Deformation vertex streams must have identical counts.")
    attributes = array.array("f", bytes(count * stride * 4))
    handedness = array.array("f", bytes(count * 4))
    cdef float[::1] out = attributes
    cdef float[::1] signs = handedness
    for vertex in range(count):
        sign = tangents[vertex * 4 + 3]
        if fabs(fabs(sign) - 1.0) > 0.001:
            raise ValueError("Tangent handedness must be -1 or 1.")
        nx, ny, nz = normals[vertex * 3], normals[vertex * 3 + 1], normals[vertex * 3 + 2]
        tx, ty, tz = tangents[vertex * 4], tangents[vertex * 4 + 1], tangents[vertex * 4 + 2]
        o = vertex * stride
        out[o], out[o + 1] = uv0[vertex * 2], uv0[vertex * 2 + 1]
        out[o + 2], out[o + 3], out[o + 4] = <float> nx, <float> ny, <float> nz
        out[o + 5], out[o + 6], out[o + 7] = <float> tx, <float> ty, <float> tz
        out[o + 8] = <float> ((ny * tz - nz * ty) * sign)
        out[o + 9] = <float> ((nz * tx - nx * tz) * sign)
        out[o + 10] = <float> ((nx * ty - ny * tx) * sign)
        out[o + 11], out[o + 12] = uv1[vertex * 2], uv1[vertex * 2 + 1]
        out[o + 13] = colors[vertex * color_width]
        out[o + 14] = colors[vertex * color_width + 1]
        out[o + 15] = colors[vertex * color_width + 2]
        out[o + 16] = colors[vertex * color_width + 3] if color_width == 4 else 1.0
        signs[vertex] = <float> sign
    return attributes, handedness


@cython.boundscheck(False)
@cython.wraparound(False)
def _interleave_targets(Py_ssize_t count, const float[::1] positions, const float[::1] normals, const float[::1] tangents):
    """One morph target as per-vertex position, normal and tangent deltas."""
    cdef Py_ssize_t vertex, component
    if positions.shape[0] != count * 3 or normals.shape[0] != count * 3 or tangents.shape[0] != count * 3:
        raise ValueError("Morph streams must match the base vertex count.")
    result = array.array("f", bytes(count * 36))
    cdef float[::1] out = result
    for vertex in range(count):
        for component in range(3):
            out[vertex * 9 + component] = positions[vertex * 3 + component]
            out[vertex * 9 + 3 + component] = normals[vertex * 3 + component]
            out[vertex * 9 + 6 + component] = tangents[vertex * 3 + component]
    return result


@cython.boundscheck(False)
@cython.wraparound(False)
def _pack_skin(Py_ssize_t count, Py_ssize_t sets, const double[::1] joints, const double[::1] weights):
    """Nonzero influences per vertex, set by set, normalized by their double-precision sum."""
    cdef Py_ssize_t vertex, set_index, k, position, filled = 0, size = count * 4 * sets
    cdef double weight, total
    cdef long max_joint = -1
    if joints.shape[0] != size or weights.shape[0] != size:
        raise ValueError("Skin weights must be between zero and one and match the vertex count.")
    for position in range(size):
        if not 0.0 <= weights[position] <= 1.0:
            raise ValueError("Skin weights must be between zero and one and match the vertex count.")
        if <long> joints[position] > max_joint:
            max_joint = <long> joints[position]
    starts = array.array("I", bytes((count + 1) * 4))
    indices = array.array("I", bytes(size * 4))
    normalized = array.array("f", bytes(size * 4))
    cdef unsigned int[::1] start_view = starts
    cdef unsigned int[::1] index_view = indices
    cdef float[::1] weight_view = normalized
    for vertex in range(count):
        total = 0.0
        for set_index in range(sets):
            for k in range(4):
                total += weights[(set_index * count + vertex) * 4 + k]
        if sets and total == 0.0:
            raise ValueError("Every skinned vertex requires a nonzero joint weight.")
        for set_index in range(sets):
            for k in range(4):
                position = (set_index * count + vertex) * 4 + k
                weight = weights[position]
                if weight != 0.0:
                    index_view[filled] = <unsigned int> joints[position]
                    weight_view[filled] = <float> (weight / total)
                    filled += 1
        start_view[vertex + 1] = <unsigned int> filled
    return starts, indices[:filled], normalized[:filled], max_joint


cdef class Loader:

    cdef public str dirname
    "The directory that the asset was loaded from."

    cdef const aiScene *scene
    "The scene that has been loaded."

    cdef public object model_data
    "The ModelData object that is being filled in."

    cdef public dict mesh_info
    "A dictionary mapping mesh indices to MeshInfo objects."

    cdef public bint tangents
    "True if tangents should be included in the mesh."

    cdef bint material_alpha
    cdef bint material
    cdef dict document

    cdef public set uniforms
    "A list of the uniforms being set by materials."

    cdef public dict textures
    "A map from material and texture type to the texture displayable."

    cdef public object filename
    "The name of the file being loaded."

    cdef public object import_data


    def load(
        self,
        model_data: ModelData,
        filename: str,
        tangents: bool,
        zoom: float,
        material_alpha: bool = False,
        material: bool = False,
        scene: bool = False) -> None:

        if not renpy.loader.loadable(filename):
            raise FileNotFoundError(f"GLTFModel not loadable: {filename}")

        self.filename = filename

        self.tangents = tangents
        self.material_alpha = material_alpha
        self.material = material
        self.document = _material_document(filename) if material else { }
        if scene:
            model_data.asset = renpy.gl2.gltfscene.Asset(self.document, filename)
            model_data.scene_memory_size = model_data.asset.memory_size()
            model_data.memory_size += model_data.scene_memory_size
            if renpy.config.developer:
                for warning in model_data.asset.warnings:
                    renpy.display.log.write("GLTFModel %r: %s", filename, warning)
        model_data.lights = renpy.gl2.gltf.import_lights(self.document, zoom) if material else ()
        self.uniforms = set()

        self.textures = { }
        if material and (model_data.lights or scene) and not self.document.get("meshes"):
            model_data.filename = filename
            model_data.mesh_info = [ ]
            self.document = { }
            return

        self.dirname = filename.rpartition("/")[0]

        # Load the scene.
        filename_bytes = filename.encode()
        cdef const char *filename_c = filename_bytes
        cdef unsigned int flags = aiProcessPreset_TargetRealtime_Quality | aiProcess_FlipUVs | aiProcess_FlipWindingOrder
        if not tangents:
            flags &= ~aiProcess_CalcTangentSpace
        if material:
            # Preserve authored material indices and valid constant UV/color attributes.
            flags &= ~(aiProcess_RemoveRedundantMaterials | aiProcess_FindInvalidData)
            flags |= aiProcess_ValidateDataStructure
        if scene and model_data.asset.geometries:
            # Source primitive identities must survive alongside the direct deformation streams.
            flags &= ~aiProcess_SplitLargeMeshes

        # File callbacks reacquire the GIL; native import and postprocessing need not hold it.
        self.import_data = renpy.gl2.gltfscene.material_import_document(self.document, filename) if scene and model_data.asset.geometries else None
        try:
            with nogil:
                self.scene = assimpio_import(filename_c, flags)
        finally:
            self.import_data = None

        if not self.scene:
            self.document = { }
            if error := assimp_get_error():
                raise Exception("Error loading %s: %s" % (filename, error))
            else:
                raise Exception("Error loading %s: Unknown error" % filename)

        try:

            self.model_data = model_data
            self.mesh_info = { }

            self.load_textures()

            # The base matrix uses scale and rotate to move the model into the Ren'Py coordinate system,
            # and then scales it.
            m = Matrix.scale(-1, -1, -1) * Matrix.rotate(0, 180, 0) *  Matrix.scale(zoom, zoom, zoom)

            if scene:
                roots = model_data.asset.roots
                if len(roots) == 1:
                    self.load_node(self.scene.mRootNode, m, roots[0])
                elif roots:
                    if self.scene.mRootNode.mNumChildren != len(roots):
                        raise ValueError("Assimp changed the source glTF root hierarchy.")
                    for i, root in enumerate(roots):
                        self.load_node(self.scene.mRootNode.mChildren[i], m, root)
            else:
                self.load_node(self.scene.mRootNode, m)

            self.model_data.filename = filename
            self.model_data.mesh_info = list(self.mesh_info.values())

            return self.model_data

        finally:
            self.mesh_info = { }
            self.model_data = None
            self.uniforms = set()
            self.document = { }

            aiReleaseImport(self.scene)
            self.scene = NULL


    def load_textures(self) -> None:
        """
        Loads the textures from the scene.
        """

        cdef aiTexture *texture
        cdef char *texbytes

        for i in range(self.scene.mNumTextures):
            texture = self.scene.mTextures[i]

            key = f"*{i}"
            format = texture.achFormatHint.decode()

            if texture.mHeight == 0:
                texbytes = <char *>texture.pcData
                data = texbytes[:texture.mWidth]
                self.model_data.memory_size += len(data)

                filename = key + "." + format

                self.model_data.embedded_textures[key] = unoptimized_texture(
                    Data(data, filename), premultiply=not self.material_alpha)

            else:
                raise Exception(f"{format} textures are not (yet) supported.")

    def get_texture(self, material_index: int, texture_type: int, texture_index: int = 0) -> Displayable | None:
        """
        Given a material index and a texture type, returns the path to the texture. This
        may also be *0 (etc) for embedded textures.
        """

        key = (material_index, texture_type, texture_index)

        if key in self.textures:
            return self.textures[key]

        cdef aiString path_string
        cdef aiMaterial *material = self.scene.mMaterials[material_index]

        if aiGetMaterialTextureCount(material, <aiTextureType>texture_type) == 0:
            rv = None
        else:
            status = aiGetMaterialTexture(
                material,
                <aiTextureType>texture_type,
                texture_index,
                &path_string,
                NULL,
                NULL,
                NULL,
                NULL,
                NULL,
                NULL)
            if status:
                self.textures[key] = None
                return None
            path = path_string.data[:path_string.length].decode()

            if path.startswith("*"):
                d = loader.model_data.embedded_textures[path]

            else:
                d = renpy.easy.displayable(loader.dirname + "/" + path)

            rv = unoptimized_texture(d, premultiply=not self.material_alpha)

        self.textures[key] = rv

        return rv

    def document_texture(self, texture_index):
        """Read extension images directly, independent of the installed Assimp version's extension coverage."""
        from urllib.parse import unquote, urlsplit
        import posixpath
        images = self.document.get("images", [])
        texture = self.document["textures"][texture_index]
        image_index = renpy.gl2.gltfscene.index(texture.get("source"), images, "image")
        key = ("image", image_index)
        if key in self.textures:
            return self.textures[key]
        image = images[image_index]
        uri = image.get("uri")
        if uri is not None and not isinstance(uri, str):
            raise ValueError("Material image URI must be a string.")
        data = None
        if uri is not None and not uri.startswith("data:"):
            parts = urlsplit(uri)
            path = unquote(parts.path)
            if parts.scheme or parts.netloc or parts.query or parts.fragment or path.startswith("/") or ":" in path or "\\" in path:
                raise ValueError("Material images require relative local resource URIs.")
            rv = unoptimized_texture(renpy.display.im.Image(posixpath.normpath(posixpath.join(self.dirname, path))),
                premultiply=False)
        else:
            if uri is not None:
                head, separator, encoded = uri.partition(",")
                if not separator or not head.endswith(";base64"):
                    raise ValueError("Material image data URIs must use base64 encoding.")
                data = base64.b64decode(encoded, validate=True)
            else:
                views = self.document.get("bufferViews", [])
                view = views[renpy.gl2.gltfscene.index(image.get("bufferView"), views, "image bufferView")]
                buffer = renpy.gl2.gltfscene.Accessors(self.document, self.filename).buffer(view.get("buffer"))
                start, length = view.get("byteOffset", 0), view.get("byteLength")
                if type(start) is not int or type(length) is not int or start < 0 or length <= 0 or start + length > len(buffer):
                    raise ValueError("Material image exceeds its buffer view.")
                data = bytes(buffer[start:start+length])
            suffix = ".png" if data.startswith(b"\x89PNG\r\n\x1a\n") else ".jpg" if data.startswith(b"\xff\xd8\xff") else None
            if suffix is None:
                raise ValueError("Embedded material images require PNG or JPEG data.")
            rv = None
            for existing in self.model_data.embedded_textures.values():
                source = existing
                while isinstance(source, renpy.display.im.UnoptimizedTexture):
                    source = source.image
                if isinstance(source, Data) and source.data == data:
                    rv = existing
                    break
            if rv is None:
                rv = unoptimized_texture(Data(data, "gltf-image" + suffix), premultiply=False)
                self.model_data.embedded_textures[f"image:{image_index}"] = rv
                self.model_data.memory_size += len(data)
        self.textures[key] = rv
        return rv

    def get_texture_uniforms(
        self, material_index: int, material_uniforms: dict, properties: dict
    ) -> dict[str, Displayable]:
        """
        Given a material index, returns a dictionary of texture uniforms that
        should be set for the material.
        """

        uniforms = { }

        if self.material:
            source = self.get_material(material_index)
            pbr = source.get("pbrMetallicRoughness", { })
            for uniform, (name, texture_type, srgb, texture_index) in MATERIAL_TEXTURES.items():
                location = pbr if name in ("baseColorTexture", "metallicRoughnessTexture") else source
                if name in MATERIAL_TEXTURE_EXTENSIONS:
                    location = source.get("extensions", { }).get(MATERIAL_TEXTURE_EXTENSIONS[name], { })
                info = location.get(name)
                binding = "u_material_" + uniform[6:]
                material_uniforms[binding + "_present"] = float(info is not None)
                coord = 0
                transform = { }
                sampler = { }
                if info is not None:
                    textures = self.document.get("textures", [ ])
                    index = info["index"]
                    if type(index) is not int or not 0 <= index < len(textures):
                        raise ValueError(f"Invalid {name} texture index: {index!r}")
                    texture = textures[index]
                    if "sampler" in texture:
                        samplers = self.document.get("samplers", [ ])
                        index = texture["sampler"]
                        if type(index) is not int or not 0 <= index < len(samplers):
                            raise ValueError(f"Invalid {name} sampler index: {index!r}")
                        sampler = samplers[index]
                    transform = info.get("extensions", { }).get("KHR_texture_transform", { })
                    coord = transform.get("texCoord", info.get("texCoord", 0))
                    if type(coord) is not int or coord not in (0, 1):
                        raise ValueError(f"{name} requires unsupported UV set {coord!r}; supported sets are 0 and 1.")

                offset = _material_vector(transform.get("offset", (0, 0)), 2, name + " offset")
                scale = _material_vector(transform.get("scale", (1, 1)), 2, name + " scale")
                angle = transform.get("rotation", 0)
                if type(angle) not in (int, float) or not math.isfinite(angle):
                    raise ValueError(f"{name} rotation must be finite.")
                c, s = math.cos(angle), math.sin(angle)
                material_uniforms[binding + "_texcoord"] = float(coord)
                material_uniforms[binding + "_uv0"] = (c * scale[0], -s * scale[1], offset[0])
                material_uniforms[binding + "_uv1"] = (s * scale[0], c * scale[1], offset[1])

                wrap = (sampler.get("wrapS", 10497), sampler.get("wrapT", 10497))
                mag, min_filter = sampler.get("magFilter", 9729), sampler.get("minFilter", 9987)
                if any(v not in (10497, 33071, 33648) for v in wrap):
                    raise ValueError(f"Invalid {name} wrap modes: {wrap!r}")
                if mag not in (9728, 9729) or min_filter not in (9728, 9729, 9984, 9985, 9986, 9987):
                    raise ValueError(f"Invalid {name} texture filters: {(mag, min_filter)!r}")
                suffix = uniform[2:]
                properties["texture_wrap_" + suffix] = wrap
                properties["texture_scaling_" + suffix] = (mag, min_filter)

                d = ((self.document_texture(info["index"]) if texture_type < 0
                    else self.get_texture(material_index, texture_type, texture_index)) if info is not None else None)
                if info is not None and d is None:
                    raise ValueError(f"Assimp could not load {name} in {self.filename!r}.")
                if d is None:
                    d = renpy.display.im.Null("#8080ff" if name in ("normalTexture", "clearcoatNormalTexture") else "#fff")
                # GL2 sampler state belongs to the texture, so distinct samplers need distinct cached textures.
                uniforms[uniform] = renpy.display.im.UnoptimizedTexture(
                    d, premultiply=False, srgb=srgb, mipmap=min_filter >= 9984,
                    texture_sampler=wrap + (mag, min_filter))
            return uniforms

        for uniform, texture_type in TEXTURE_TYPES.items():

            d = self.get_texture(material_index, texture_type)
            if d is not None:
                uniforms[uniform] = d

        return uniforms

    def get_material(self, material_index: int):
        """Assimp appends its default material after the document's authored materials."""
        materials = self.document.get("materials", [ ])
        return materials[material_index] if material_index < len(materials) else { }

    def get_material_uniforms(self, material_index: int):

        cdef float[4] values
        cdef int ivalue
        cdef unsigned int pmax
        cdef aiString alpha_mode

        cdef aiMaterial *material = self.scene.mMaterials[material_index]

        uniforms = { }

        if self.material_alpha:
            uniforms.update(u_material_gltf_alphamode=0.0, u_material_gltf_alphacutoff=0.5)
            if not aiGetMaterialString(material, "$mat.gltf.alphaMode", 0, 0, &alpha_mode):
                mode = alpha_mode.data[:alpha_mode.length].decode()
                uniforms["u_material_gltf_alphamode"] = { "OPAQUE": 0.0, "MASK": 1.0, "BLEND": 2.0 }.get(mode, 0.0)

        for i in range(material.mNumProperties):
            pmax = 4

            prop = material.mProperties[i]

            key = prop.mKey.data[:prop.mKey.length].decode()

            prefix, _, suffix = key.partition(".")
            suffix = suffix.lower().replace(".", "_")

            if prop.mType != aiPTI_Float and prop.mType != aiPTI_Double and prop.mType != aiPTI_Integer and prop.mType != aiPTI_Buffer:
                continue

            if prefix == "$mat":
                name = f"u_material_{suffix}"
            elif prefix == "$clr":
                name = f"u_color_{suffix}"
            else:
                continue

            pmax = 4
            if aiGetMaterialFloatArray(material, prop.mKey.data, 0, 0, &values[0], &pmax):
                continue

            if pmax == 1:
                value = values[0]
            elif pmax == 2:
                value = (values[0], values[1])
            elif pmax == 3:
                value = (values[0], values[1], values[2])
            elif pmax == 4:
                value = (values[0], values[1], values[2], values[3])
            else:
                continue

            uniforms[name] = value

        if self.material:
            source = self.get_material(material_index)
            pbr = source.get("pbrMetallicRoughness", { })
            uniforms["u_color_base"] = _material_vector(pbr.get("baseColorFactor", (1, 1, 1, 1)), 4, "baseColorFactor")
            uniforms["u_material_metallicfactor"] = pbr.get("metallicFactor", 1.0)
            uniforms["u_material_roughnessfactor"] = pbr.get("roughnessFactor", 1.0)
            uniforms["u_material_emissivefactor"] = _material_vector(
                source.get("emissiveFactor", (0, 0, 0)), 3, "emissiveFactor")
            emission = source.get("extensions", { }).get("KHR_materials_emissive_strength", { })
            if not isinstance(emission, dict):
                raise ValueError("KHR_materials_emissive_strength must be an object.")
            uniforms["u_material_emissive_strength"] = _material_vector(
                (emission.get("emissiveStrength", 1.0),), 1, "emissiveStrength")[0]
            if uniforms["u_material_emissive_strength"] < 0:
                raise ValueError("emissiveStrength must be nonnegative.")
            if ("KHR_materials_emissive_strength" in source.get("extensions", { })
                    and "KHR_materials_unlit" in source.get("extensions", { })):
                raise ValueError("KHR_materials_emissive_strength cannot be combined with KHR_materials_unlit.")
            uniforms["u_material_normal_scale"] = source.get("normalTexture", { }).get("scale", 1.0)
            uniforms["u_material_occlusion_strength"] = source.get("occlusionTexture", { }).get("strength", 1.0)
            uniforms["u_material_unlit"] = float("KHR_materials_unlit" in source.get("extensions", { }))
            uniforms["u_material_twosided"] = float(source.get("doubleSided", False))
            uniforms["u_material_receive_shadows"] = 1.0
            extensions = source.get("extensions", { })
            for extension in ("KHR_materials_transmission", "KHR_materials_volume", "KHR_materials_ior",
                    "KHR_materials_specular", "KHR_materials_clearcoat", "KHR_materials_sheen"):
                if extension in extensions and not isinstance(extensions[extension], dict):
                    raise ValueError(f"{extension} must be an object.")
                if extension in extensions and "KHR_materials_unlit" in extensions:
                    raise ValueError(f"{extension} cannot be combined with KHR_materials_unlit.")
            transmission = extensions.get("KHR_materials_transmission", { })
            volume = extensions.get("KHR_materials_volume", { })
            uniforms["u_material_transmission_factor"] = renpy.gl2.gltf.number(
                transmission.get("transmissionFactor", 0), "transmissionFactor", 0, 1)
            uniforms["u_material_ior"] = renpy.gl2.gltf.number(
                extensions.get("KHR_materials_ior", { }).get("ior", 1.5), "ior", 0)
            if 0.0 < uniforms["u_material_ior"] < 1.0:
                raise ValueError("ior must be zero or at least one.")
            uniforms["u_material_thickness_factor"] = renpy.gl2.gltf.number(
                volume.get("thicknessFactor", 0), "thicknessFactor", 0)
            attenuation = 0.0
            if "attenuationDistance" in volume:
                attenuation = renpy.gl2.gltf.number(volume["attenuationDistance"], "attenuationDistance", 0)
                if not attenuation:
                    raise ValueError("attenuationDistance must be positive.")
            uniforms["u_material_attenuation_distance"] = attenuation
            absorption = renpy.gl2.gltf.vector(volume.get("attenuationColor", (1, 1, 1)), "attenuationColor")
            if min(absorption) < 0 or max(absorption) > 1:
                raise ValueError("attenuationColor must be between zero and one.")
            uniforms["u_material_attenuation_color"] = absorption
            specular = extensions.get("KHR_materials_specular", {})
            coat = extensions.get("KHR_materials_clearcoat", {})
            sheen = extensions.get("KHR_materials_sheen", {})
            for name, value in (
                ("specular_factor", specular.get("specularFactor", 1.0)),
                ("clearcoat_factor", coat.get("clearcoatFactor", 0.0)),
                ("clearcoat_roughness_factor", coat.get("clearcoatRoughnessFactor", 0.0)),
                ("sheen_roughness_factor", sheen.get("sheenRoughnessFactor", 0.0)),
            ):
                uniforms["u_material_" + name] = renpy.gl2.gltf.number(value, name, 0, 1)
            for name, value, maximum in (
                ("specular_color_factor", specular.get("specularColorFactor", (1, 1, 1)), None),
                ("sheen_color_factor", sheen.get("sheenColorFactor", (0, 0, 0)), 1),
            ):
                color = renpy.gl2.gltf.vector(value, name)
                if min(color) < 0 or (maximum is not None and max(color) > maximum):
                    raise ValueError(f"{name} is outside its supported range.")
                uniforms["u_material_" + name] = color
            uniforms["u_material_clearcoat_normal_scale"] = renpy.gl2.gltf.number(
                coat.get("clearcoatNormalTexture", {}).get("scale", 1), "clearcoat normal scale")
            for name in ("u_material_metallicfactor", "u_material_roughnessfactor",
                         "u_material_normal_scale", "u_material_occlusion_strength"):
                uniforms[name] = _material_vector((uniforms[name],), 1, name)[0]
        return uniforms

    def get_twosided(self, material_index: int) -> bool:
        """
        Returns True if the material with the given index is two-sided.
        """

        cdef aiMaterial *material = self.scene.mMaterials[material_index]
        cdef int ivalue

        if aiGetMaterialIntegerArray(material, "$mat.twosided", 0, 0, &ivalue, NULL):
            return False

        return ivalue != 0


    cdef load_node(self, aiNode *node, matrix: Matrix, int source_index=-1):
        cdef unsigned int i

        node_matrix = Matrix((
            node.mTransformation.a1, node.mTransformation.a2, node.mTransformation.a3, node.mTransformation.a4,
            node.mTransformation.b1, node.mTransformation.b2, node.mTransformation.b3, node.mTransformation.b4,
            node.mTransformation.c1, node.mTransformation.c2, node.mTransformation.c3, node.mTransformation.c4,
            node.mTransformation.d1, node.mTransformation.d2, node.mTransformation.d3, node.mTransformation.d4,
            ))

        matrix = matrix * node_matrix

        source_mesh = self.model_data.asset.nodes[source_index].get("mesh") if source_index >= 0 else None
        direct = source_mesh is not None and any(key[0] == source_mesh for key in self.model_data.asset.geometries)
        if direct and node.mNumMeshes != len(self.model_data.asset.meshes[source_mesh]["primitives"]):
            raise ValueError("Assimp changed a deforming glTF primitive's source identity.")
        for i in range(node.mNumMeshes):
            key = source_mesh, i
            mesh_info = (self.load_source_mesh(key) if direct and key in self.model_data.asset.geometries
                else self.load_mesh(node.mMeshes[i]))

            if mesh_info is None:
                continue

            self.model_data.blit_info.append(BlitInfo(
                matrix,
                matrix.inverse(),
                mesh_info, source_index))

        if source_index >= 0:
            children = self.model_data.asset.nodes[source_index].get("children", [])
            if node.mNumChildren != len(children):
                raise ValueError("Assimp changed the source glTF node hierarchy.")
        for i in range(node.mNumChildren):
            self.load_node(node.mChildren[i], matrix, children[i] if source_index >= 0 else -1)


    def load_source_mesh(self, key):
        """Keep morph/joint identities independent of native vertex deduplication and bone limits."""
        cache_key = ("source", key)
        if cache_key in self.mesh_info:
            return self.mesh_info[cache_key]
        source = self.model_data.asset.geometries[key]
        mesh = _deform_geometry(source, (0,) * source.target_count, ())
        info = MeshInfo(self, mesh, source.material, self.textures)
        info.deformation = source
        for uniform in info.texture_uniforms:
            binding = "u_material_" + uniform[6:]
            if info.uniforms[binding + "_present"] and int(info.uniforms[binding + "_texcoord"]) not in source.uv_sets:
                raise ValueError(f"{self.filename!r}: {uniform} requires a missing UV set.")
        self.model_data.memory_size += source.count * (3 + mesh.layout.stride) * sizeof(float) + len(source.triangles) * 4
        self.mesh_info[cache_key] = info
        return info


    def load_mesh(self, mesh_index: int) -> MeshInfo:
        """
        Loads the mesh with index `mesh_index` from the scene.
        """

        cached = self.mesh_info.get(mesh_index, None)
        if cached is not None:
            return cached

        if mesh_index < 0 or mesh_index >= self.scene.mNumMeshes:
            raise Exception("Invalid mesh index %d." % mesh_index)

        cdef aiMesh *mesh = self.scene.mMeshes[mesh_index]

        def log(msg: str):
            if renpy.config.developer:
                renpy.display.log.write("%s", msg)

        if mesh.mPrimitiveTypes == aiPrimitiveType_LINE:
            log(f"Warning: {self.filename!r}, mesh {mesh_index} is a line mesh, which is not supported. Skipping.")
            return

        if mesh.mPrimitiveTypes == aiPrimitiveType_POINT:
            log(f"Warning: {self.filename!r}, mesh {mesh_index} is a point mesh, which is not supported. Skipping.")
            return

        if mesh.mPrimitiveTypes != aiPrimitiveType_TRIANGLE:
            log(f"Warning: {self.filename!r}, mesh {mesh_index} is not a triangle mesh (type={mesh.mPrimitiveTypes}). Skipping.")
            return

        if self.tangents:
            layout = renpy.gl2.gl2mesh.GLTF_T_LAYOUT if self.material else renpy.gl2.gl2mesh.MODEL_NT_LAYOUT
        else:
            layout = renpy.gl2.gl2mesh.GLTF_LAYOUT if self.material else renpy.gl2.gl2mesh.MODEL_N_LAYOUT
        cdef int stride = layout.stride

        cdef Mesh3 m = Mesh3(layout, mesh.mNumVertices, mesh.mNumFaces)
        m.points = mesh.mNumVertices
        m.triangles = mesh.mNumFaces

        cdef Point3 *point = m.point
        cdef float *attribute = m.attribute

        cdef unsigned int i
        cdef int component
        cdef unsigned int *triangle = m.triangle
        cdef bint tangents = self.tangents
        cdef int extra = 11 if tangents else 5
        cdef float minx = float("inf"), miny = minx, minz = minx
        cdef float maxx = -minx, maxy = maxx, maxz = maxx

        # The copies are plain C, so the main thread can draw while a preload thread imports.
        with nogil:
            if self.material:
                memset(attribute, 0, mesh.mNumVertices * stride * sizeof(float))
            for i in range(mesh.mNumVertices):
                point[i].x = mesh.mVertices[i].x
                point[i].y = mesh.mVertices[i].y
                point[i].z = mesh.mVertices[i].z
                minx = fminf(minx, point[i].x)
                miny = fminf(miny, point[i].y)
                minz = fminf(minz, point[i].z)
                maxx = fmaxf(maxx, point[i].x)
                maxy = fmaxf(maxy, point[i].y)
                maxz = fmaxf(maxz, point[i].z)

                # a_tex_coord
                if mesh.mTextureCoords[0]:
                    attribute[0] = mesh.mTextureCoords[0][i].x
                    attribute[1] = mesh.mTextureCoords[0][i].y

                # a_normal
                if mesh.mNormals:
                    attribute[2] = mesh.mNormals[i].x
                    attribute[3] = mesh.mNormals[i].y
                    attribute[4] = mesh.mNormals[i].z

                if tangents:
                    # a_tangent
                    if mesh.mTangents:
                        attribute[5] = mesh.mTangents[i].x
                        attribute[6] = mesh.mTangents[i].y
                        attribute[7] = mesh.mTangents[i].z

                    # a_bitangent
                    if mesh.mBitangents:
                        attribute[8] = mesh.mBitangents[i].x
                        attribute[9] = mesh.mBitangents[i].y
                        attribute[10] = mesh.mBitangents[i].z

                if self.material:
                    if mesh.mTextureCoords[1]:
                        attribute[extra] = mesh.mTextureCoords[1][i].x
                        attribute[extra + 1] = mesh.mTextureCoords[1][i].y
                    attribute[extra + 2] = attribute[extra + 3] = attribute[extra + 4] = attribute[extra + 5] = 1.0
                    if mesh.mColors[0]:
                        attribute[extra + 2] = mesh.mColors[0][i].r
                        attribute[extra + 3] = mesh.mColors[0][i].g
                        attribute[extra + 4] = mesh.mColors[0][i].b
                        attribute[extra + 5] = mesh.mColors[0][i].a
                    if not (isfinite(point[i].x) and isfinite(point[i].y) and isfinite(point[i].z)):
                        raise ValueError("glTF positions must contain finite numbers.")
                    for component in range(stride):
                        if not isfinite(attribute[component]):
                            raise ValueError("glTF vertex attributes must contain finite numbers.")

                attribute += stride

            for i in range(mesh.mNumFaces):
                triangle[0] = mesh.mFaces[i].mIndices[0]
                triangle[1] = mesh.mFaces[i].mIndices[1]
                triangle[2] = mesh.mFaces[i].mIndices[2]

                triangle += 3

        m.point_version = 1
        m.bounds = (minx, miny, minz, maxx, maxy, maxz)
        m.attribute_version = 1
        m.triangle_version = 1

        self.model_data.memory_size += (
            mesh.mNumVertices * (sizeof(Point3) + stride * sizeof(float))
            + mesh.mNumFaces * 3 * sizeof(unsigned int))

        info = MeshInfo(
            self,
            m,
            mesh.mMaterialIndex,
            self.textures)

        if self.material:
            for uniform in info.texture_uniforms:
                binding = "u_material_" + uniform[6:]
                if info.uniforms[binding + "_present"]:
                    coord = int(info.uniforms[binding + "_texcoord"])
                    if not mesh.mTextureCoords[coord]:
                        raise ValueError(f"{self.filename!r}: {uniform} requires missing UV set {coord}.")

        self.mesh_info[mesh_index] = info
        return info

loader = Loader()
"The loader used to load in imported models."

loader_lock = threading.Lock()
"The lock used to protect the loader."

class GLTFModel(Displayable):
    """
    :doc: assimp

    A displayable that loads a 3D Model in the GLTF format. This format is supported by many 3D tools. Ren'Py
    uses the `Open Asset Importer (assimp) library <https://github.com/assimp/assimp>`_ to load GLTF models.

    For the purposes of Ren'Py's 2D layout system, a GLTFModel has zero width and height. By default, the model
    is loaded at the size found in the file that contains it. If required, the `zoom` may be used to scale it.

    When multiple models are in use, the ``gl_depth True`` property should be supplied to the camera, so that
    depth testing is enabled. By default every mesh is submitted for drawing.

    `filename`
        The filename of the model to display.

    `shader`
        Either a string or tuple of strings, giving the name of the shader to use.
        With `material` True, omitting this selects ``renpy.gltf.unlit``.

    `tangents`
        If True, tangents are generated when needed and included in the mesh. Otherwise, tangent generation is skipped.
        The built-in PBR shader always enables this.

    `zoom`
        A zoom factor that will be applied to the model. Many models naturally use the range -1 to 1, and so this
        may need to be quite large to make the model visible.

    `report`
        If true, a report of the model will be printed to the log. This includes the uniforms that are used by
        the model.

    `frustum_cull`
        If True, skips meshes whose imported bounds are outside the camera's view.
        Leave False for shaders that move vertices beyond those bounds.

    `material_alpha`
        If True, loads material textures without alpha premultiplication and sorts
        blended meshes back to front within the enclosing ``gl_depth`` scene.
        The shader must handle the material alpha modes and produce premultiplied
        output, as in the example below. False preserves the existing texture and
        drawing behavior.

    `material`
        If True, uses the standard glTF material contract, including two UV sets,
        vertex colors, per-texture transforms and samplers, linear data textures,
        and sRGB color textures. This implies `material_alpha` True. If no shader
        is supplied, selects the built-in unlit material. Explicit model uniforms
        and texture overrides take precedence over imported material values.
        Selecting ``renpy.gltf.unlit`` or ``renpy.gltf.pbr`` also enables this option.
        The PBR shader requires an enclosing :class:`GLTFScene`.

    `light_overrides`
        Optional dictionary keyed by the names of imported glTF lights. Each
        value is a dictionary overriding ``color``, ``intensity``, ``range``,
        ``inner_cone_angle``, ``outer_cone_angle``, or ``cast_shadows``.
        Requires the standard material import path. Unknown names raise an
        error; overrides do not mutate the shared import.

    `cast_shadows`, `receive_shadows`
        Whether this model casts and receives shadows in a :class:`GLTFScene`.
        Both default to True. Blended and optically transmitting texels do
        not cast shadows.

    `scene`
        If True, retains named nodes, material identities, skinning, morph targets, and animation
        from the active glTF scene. Implies `material` True. The default False
        preserves the original static import. Use ``model.scene_info()`` to
        inspect source indices, names, hierarchy, clips, durations, and warnings.

    `node_overrides`
        Dictionary keyed by a unique node name or numeric source index. Values
        override local ``translation``, XYZW quaternion ``rotation``, ``scale``,
        a column-major 16-number ``matrix``, morph ``weights``, or boolean ``visible``. Local values
        use glTF coordinates and units before model zoom. Hidden nodes hide
        their descendants, including lights. Overrides take precedence over
        animation. Matrix and TRS cannot be combined on a node.

    `material_overrides`
        Dictionary keyed by a unique material name or numeric source index.
        Values supply ``u_`` uniforms and ``u_tex_`` texture overrides using
        the standard material contract. They override whole-model settings.
        Both override dictionaries automatically enable `scene` and are
        copied during construction. Recreate the displayable to change them.

    `animation`
        Optional clip name or numeric index. Automatically enables `scene`.
        Plays node translation, rotation, scale, and morph weights from shown-time,
        using STEP, LINEAR, or CUBICSPLINE interpolation. Joint nodes drive
        linear-blend skinning. Animation-pointer extensions are unsupported.

    `animation_time`
        Optional fixed clip time in seconds, for seeking or paused poses.
        None (the default) plays automatically. Fixed time bypasses speed and
        offset. Clips clamp at their first/last key unless looping is enabled.

    `animation_loop`, `animation_speed`, `animation_offset`
        Looping defaults to False. Speed defaults to 1 and may be negative;
        offset defaults to 0 seconds. Automatic clip time is elapsed shown-time
        times speed plus offset. A running saved instance resumes from its
        saved elapsed time; Ren'Py can re-execute a show statement on load.

    `animation_blend`
        Optional ``(secondary clip name/index, weight)`` pair. Weight ranges
        from zero to one. Missing channels blend with their bind-pose values.
        Rotations use shortest-path spherical interpolation. Enables `scene`.

    `animation_blend_time`
        Optional fixed secondary clip time. None shares the primary clip time.

    `animation_blend_duration`
        Nonnegative crossfade duration in elapsed shown seconds. Zero applies
        the requested weight immediately; positive values ramp from zero to
        that weight, independently of clip speed and pinned animation times.
    """

    filename: str
    "The filename of the model to display."

    frustum_cull = False
    material_alpha = False
    material = False
    light_overrides = ()
    cast_shadows = True
    scene = False
    node_overrides = ()
    material_overrides = ()
    animation = None
    animation_time = None
    animation_loop = False
    animation_speed = 1.0
    animation_offset = 0.0
    animation_blend = None
    animation_blend_time = None
    animation_blend_duration = 0.0
    _animation_elapsed = 0.0
    _animation_resume = 0.0
    _animation_origin = 0.0
    _animation_rebase = False

    shaders: Iterable[str]
    "The shaders to use."

    tangents: bool
    "True if tangents should be included in the mesh."

    zoom: float
    "The zoom level of the model."

    uniforms: dict[str, object]
    """
    A dictionary of uniforms that are set by the model.
    """

    texture_uniforms: dict[str, Displayable]
    """
    A dictionary of texture uniforms.
    """

    report: bool
    """
    Should a report of the model be printed to the log? This includes the uniforms that are used by the model.
    """

    def __init__(
        self,
        filename: str,
        shader: str|tuple[str] = tuple(),
        tangents: bool = False,
        zoom: float = 1.0,
        report: bool = False,
        *,
        frustum_cull: bool = False,
        material_alpha: bool = False,
        material: bool = False,
        light_overrides=None,
        cast_shadows=True,
        receive_shadows=True,
        scene=False,
        node_overrides=None,
        material_overrides=None,
        animation=None,
        animation_time=None,
        animation_loop=False,
        animation_speed=1.0,
        animation_offset=0.0,
        animation_blend=None,
        animation_blend_time=None,
        animation_blend_duration=0.0,
        **kwargs):

        super().__init__()

        if isinstance(shader, str):
            shaders = (shader, )
        else:
            shaders = shader

        if type(scene) is not bool or type(animation_loop) is not bool:
            raise TypeError("scene and animation_loop must be bools.")
        if animation is not None and type(animation) not in (str, int):
            raise TypeError("animation must be a clip name or numeric index.")
        self.animation_blend = renpy.gl2.gltfscene.blend_clip(animation_blend)
        self.animation_blend_time = (None if animation_blend_time is None
            else renpy.gl2.gltf.number(animation_blend_time, "animation_blend_time"))
        self.animation_blend_duration = renpy.gl2.gltf.number(animation_blend_duration, "animation_blend_duration", 0)
        if self.animation_blend is None and (animation_blend_time is not None or animation_blend_duration):
            raise ValueError("Blend time and duration require animation_blend.")
        self.scene = scene or bool(node_overrides) or bool(material_overrides) or animation is not None or self.animation_blend is not None
        self.node_overrides = renpy.gl2.gltfscene.node_overrides(node_overrides)
        if material_overrides is not None and not isinstance(material_overrides, dict):
            raise TypeError("material_overrides must be a dictionary.")
        if any(type(key) not in (str, int) or not isinstance(values, dict)
               for key, values in (material_overrides or {}).items()):
            raise ValueError("Material overrides need a name/index and a dictionary of uniforms and textures.")
        self.material_overrides = tuple((key, *_standard_overrides(values))
            for key, values in (material_overrides or {}).items())
        self.animation = animation
        self.animation_time = (None if animation_time is None
            else renpy.gl2.gltf.number(animation_time, "animation_time"))
        self.animation_loop = animation_loop
        self.animation_speed = renpy.gl2.gltf.number(animation_speed, "animation_speed")
        self.animation_offset = renpy.gl2.gltf.number(animation_offset, "animation_offset")
        self._duplicatable = (animation is not None or self.animation_blend is not None) and (animation_time is None or self.animation_blend_duration > 0)
        self._save_target = self._duplicatable
        material = material or self.scene or any(i in shaders for i in ("renpy.gltf.unlit", "renpy.gltf.pbr"))
        if material and not shaders:
            shaders = ("renpy.gltf.unlit", )
        if not shaders:
            raise ValueError("At least one shader must be specified for GLTFModel. Consider 'renpy.texture' to start with.")

        self.filename = filename
        self.shaders = shaders
        self.tangents = tangents or "renpy.gltf.pbr" in shaders
        self.zoom = zoom
        self.report = report
        self.frustum_cull = frustum_cull
        self.material_alpha = material_alpha
        self.material = material
        if light_overrides is not None and not isinstance(light_overrides, dict):
            raise ValueError("light_overrides must be a dictionary keyed by light name.")
        if light_overrides and not material:
            raise ValueError("light_overrides requires the standard material import path.")
        if any(not isinstance(name, str) or not isinstance(values, dict)
               for name, values in (light_overrides or {}).items()):
            raise ValueError("Each light override needs a string name and a dictionary of settings.")
        self.light_overrides = tuple((name, tuple(values.items())) for name, values in (light_overrides or {}).items())
        if type(cast_shadows) is not bool or type(receive_shadows) is not bool:
            raise ValueError("cast_shadows and receive_shadows must be bools.")
        self.cast_shadows = cast_shadows
        if material:
            self.material_alpha = True

        self.uniforms = { }
        if material:
            self.uniforms["u_material_receive_shadows"] = float(receive_shadows)
        self.texture_uniforms = { }

        if material:
            uniforms, self.texture_uniforms = _standard_overrides(kwargs)
            self.uniforms.update(uniforms)
            kwargs = { }
        if not material:
            kwargs.setdefault("u_tex_diffuse", renpy.display.im.Null("#fff"))

        for k, v in kwargs.items():

            if k.startswith("u_tex_"):
                d = renpy.easy.displayable(v)
                self.texture_uniforms[k] = renpy.display.im.unoptimized_texture(d, premultiply=not material_alpha)
            elif k.startswith("u_"):
                self.uniforms[k] = v
            else:
                raise ValueError(f"Unknown keyword argument {k!r} for GLTFModel. Uniforms must start with 'u_'.")

        if report:
            self.load().report()
            self.report = False

    @property
    def cache_key(self):
        """
        The import options that determine the imported data, so equal models share one import.
        """

        key = (self.filename, self.tangents, self.zoom, self.material_alpha, self.material)
        return key + (True,) if self.scene else key

    def load(self):
        """
        Loads the AssimpModel, including its meshes and textures.
        """

        key = self.cache_key

        with cache_lock:
            model_data = cache.get(key)
            if model_data is not None:
                cache.move_to_end(key)
                return model_data

        with loader_lock:
            # Recheck under the import lock, without blocking cache hits on that lock.
            with cache_lock:
                model_data = cache.get(key)
                if model_data is not None:
                    cache.move_to_end(key)
                    return model_data

            model_data = ModelData()
            options = (self.filename, self.tangents, self.zoom, self.material_alpha, self.material)
            if self.scene:
                options += (True,)
            loader.load(model_data, *options)
            with cache_lock:
                cache[key] = model_data

        for d in self._prepared(model_data)[3]:
            renpy.display.im.cache.preload_image(d)

        return model_data

    def _materials(self, model_data):
        if not self.material_overrides:
            return {}
        return {renpy.gl2.gltfscene.resolve(key, model_data.asset.materials, "material"): (uniforms, textures)
            for key, uniforms, textures in self.material_overrides}

    def _prepared(self, model_data):
        """Keep binding wrappers in unsaved import data, with weak ownership by their model instance."""
        key = (tuple(self.texture_uniforms.items()), self.material_overrides)
        previous = model_data.prepared.get(self)
        if previous is not None and previous[0] == key:
            return previous[1]
        materials = self._materials(model_data)
        mesh_textures = {mi: self._mesh_textures(mi, materials.get(mi.material_index, ({}, {}))[1])
            for mi in model_data.mesh_info}
        # Material bindings supply their sampler and color space; the replaced parent copy is never sampled.
        root_textures = {name: texture for name, texture in self.texture_uniforms.items()
            if model_data.mesh_info and (not self.material
                or not all(name in mesh_textures[mi] for mi in model_data.mesh_info))}
        textures = list(dict.fromkeys([*root_textures.values(),
            *(texture for values in mesh_textures.values() for texture in values.values())]))
        result = materials, mesh_textures, root_textures, textures
        model_data.prepared[self] = (key, result)
        return result

    def scene_info(self):
        """Return independent source diagnostics. Requires ``scene=True``; see the scene controls in :doc:`model`."""
        if not self.scene:
            raise ValueError("scene_info requires scene=True on the model.")
        return self.load().asset.info()

    def after_setstate(self):
        # Shown-time can restart after loading; save elapsed seconds rather than an absolute process clock.
        self._animation_resume = self._animation_elapsed
        self._animation_rebase = True

    def _duplicate(self, args):
        if args and args.args:
            args.extraneous()
        if not self._duplicatable:
            return self
        rv = self._copy(args)
        rv.after_setstate()
        rv._unique()
        return rv

    def _mesh_textures(self, mesh_info, material_textures=None):
        """Resolve model overrides with the binding's sampler so shared images cannot share conflicting GL state."""
        if not self.material or not (self.texture_uniforms or material_textures):
            return mesh_info.texture_uniforms
        overrides = dict(self.texture_uniforms)
        overrides.update(material_textures or {})
        textures = dict(mesh_info.texture_uniforms)
        for name, texture in textures.items():
            if name in overrides:
                textures[name] = renpy.display.im.UnoptimizedTexture(
                    overrides[name], premultiply=False, srgb=texture.srgb,
                    mipmap=texture.mipmap, texture_sampler=texture.texture_sampler)
        return textures

    def _pose(self, model_data, st, redraw_target=None, include_weights=False):
        """The camera and mesh traversal share the saved playback clock and source hierarchy."""
        matrices, visible, weights = {}, {}, {}
        if self.scene:
            elapsed = st
            animated = self.animation is not None or self.animation_blend is not None
            if animated and (self.animation_time is None or self.animation_blend_duration > 0):
                if self._animation_rebase:
                    self._animation_origin = st
                    self._animation_rebase = False
                elapsed = st - self._animation_origin + self._animation_resume
                self._animation_elapsed = elapsed
            time = self.animation_time if self.animation_time is not None else elapsed * self.animation_speed + self.animation_offset
            blend = None
            running = self.animation_blend_duration > 0 and elapsed < self.animation_blend_duration
            clips = [(self.animation, time, self.animation_time is None)]
            if self.animation_blend is not None:
                name, weight = self.animation_blend
                if self.animation_blend_duration:
                    weight *= min(max(elapsed / self.animation_blend_duration, 0), 1)
                blend_time = time if self.animation_blend_time is None else self.animation_blend_time
                blend = name, weight, blend_time
                clips.append((name, blend_time, self.animation_time is None and self.animation_blend_time is None))
            matrices, visible, weights = model_data.asset.pose(self.node_overrides, self.animation, time,
                self.animation_loop, self.zoom, blend, True)
            for name, clip_time, dynamic in clips:
                if name is not None and dynamic and self.animation_speed:
                    clip = model_data.asset.animations[renpy.gl2.gltfscene.resolve(name, model_data.asset.animations, "animation")]
                    running |= bool(clip["duration"] and (self.animation_loop or
                        (clip_time < clip["duration"] if self.animation_speed > 0 else clip_time > 0)))
            if running:
                renpy.display.render.redraw(self if redraw_target is None else redraw_target, 0)
        return (matrices, visible, weights) if include_weights else (matrices, visible)

    def render(self, width, height, st, at):
        new_predicted.add(self)
        model_data = self.load()
        materials, mesh_textures, root_textures, textures = self._prepared(model_data)
        matrices, visible, weights = self._pose(model_data, st, include_weights=True)
        deformed = model_data.deformed.setdefault(self, {}) if self.scene else {}
        skins = {}

        renpy.display.im.cache.get_textures(textures)

        rv = Render(0, 0)
        rv.add_property("gltf_cast_shadows", self.cast_shadows)
        if self.material and (model_data.lights or self.light_overrides):
            overrides = dict(self.light_overrides)
            names = { light.light_name for light, _matrix in model_data.lights }
            if set(overrides) - names:
                raise ValueError(f"Unknown imported light override: {set(overrides) - names!r}")
            lights = model_data.lights
            if self.scene:
                lights = tuple((light, matrices[i]) for i, (light, _matrix)
                    in zip(model_data.asset.light_nodes, lights) if visible[i])
            rv.add_property("gltf_lights", tuple((light.replaced(overrides[light.light_name])
                if light.light_name in overrides else light, matrix)
                for light, matrix in lights))
            rv.add_property("gltf_imported_lights", True)

        for k, v in self.uniforms.items():
            rv.add_uniform(k, v)

        for k, v in root_textures.items():
            tr = renpy.display.im.render_for_texture(v, width, height, st, at)
            rv.add_uniform(k, tr)

        for bi in model_data.blit_info:
            if self.scene and not visible[bi.node_index]:
                continue
            mi = bi.mesh_info
            uniforms = mi.uniforms
            if self.material:
                uniforms = dict(uniforms)
                uniforms.update(self.uniforms)
                uniforms.update(materials.get(mi.material_index, ({}, {}))[0])

            cr = Render(0, 0)
            mesh = mi.mesh
            world = matrices[bi.node_index] if self.scene else bi.reverse
            if mi.deformation is not None:
                skin = model_data.asset.nodes[bi.node_index].get("skin")
                bones = ()
                if skin is not None and mi.deformation.joint_indices:
                    if skin not in skins:
                        definition = model_data.asset.skins[skin]
                        root = matrices[definition["root"]]
                        # A collapsed skeleton still produces collapsed geometry; do not invert a zero-scale root.
                        determinant = renpy.gl2.gltf.dot(renpy.gl2.gltf.cross(
                            (root.xdx, root.xdy, root.xdz), (root.ydx, root.ydy, root.ydz)), (root.zdx, root.zdy, root.zdz))
                        if determinant == 0:
                            root = Matrix.scale(self.zoom, -self.zoom, self.zoom)
                        inverse = root.inverse()
                        bones = tuple(inverse * matrices[joint] * bind for joint, bind in zip(definition["joints"], definition["inverse"]))
                        skins[skin] = root, bones
                    world, bones = skins[skin]
                morph = weights.get(bi.node_index, ())
                if bones or any(morph):
                    signature = morph, tuple(renpy.gl2.gltf.matrix_values(i) for i in bones)
                    previous = deformed.get(bi)
                    if previous is not None and previous[0] == signature:
                        mesh = previous[1]
                    else:
                        mesh = _deform_geometry(mi.deformation, morph, bones)
                        deformed[bi] = signature, mesh
            cr.mesh = mesh
            if self.frustum_cull:
                cr.add_property("frustum_cull", True)
            if self.material_alpha and uniforms.get("u_material_gltf_alphamode", 0.0) == 2.0:
                cr.add_property("transparent", True)
            if "renpy.gltf.pbr" in self.shaders and uniforms.get("u_material_transmission_factor", 0.0) > 0.0:
                cr.add_property("gltf_transmission", True)
                cr.add_property("transparent", True)
            if self.material:
                for k, v in mi.properties.items():
                    cr.add_property(k, v)
            cr.reverse = world
            cr.forward = cr.reverse.inverse() if self.scene else bi.forward

            # Imports are shared between models, so the shaders come from this one.
            for i in self.shaders:
                cr.add_shader(i)
            if "renpy.gltf.pbr" in self.shaders:
                for part in renpy.gl2.gltf.material_parts(uniforms):
                    cr.add_shader(part)

            for k, v in uniforms.items():
                cr.add_uniform(k, v)

            if ((uniforms.get("u_material_twosided", 0.0) > 0.5 if self.material else mi.twosided)
                    or (self.material and uniforms.get("u_material_thickness_factor", 0.0) > 0.0)):
                cr.add_property("cull_face", "both" if self.material else None)

            has_diffuse = False

            for k, i in (mesh_textures[mi] if self.material else mi.texture_uniforms).items():
                tr = renpy.display.im.render_for_texture(i, width, height, st, at)
                cr.add_uniform(k, tr)

                if k == ("u_tex_base_color" if self.material else "u_tex_diffuse"):
                    cr.blit(tr, (0, 0))
                    has_diffuse = True

            if not has_diffuse:
                tr = renpy.display.im.render_for_texture(self.texture_uniforms.get("u_tex_diffuse", renpy.display.im.Null("#fff")), width, height, st, at)
                cr.blit(tr, (0, 0))

            rv.blit(cr, (0, 0))

        rv.add_property("depth", True)
        rv.add_property("texture_wrap", (renpy.uguu.GL_REPEAT, renpy.uguu.GL_REPEAT))
        rv.add_property("cull_face", "ccw")

        return rv

    def visit(self):
        with cache_lock:
            model_data = cache.get(self.cache_key)
        if model_data is not None:
            return list(self._prepared(model_data)[3])
        else:
            return [ ]

    def predict_shaders(self, shaders):
        variants = {tuple(self.shaders)}
        if "renpy.gltf.pbr" in self.shaders:
            with cache_lock:
                data = cache.get(self.cache_key)
            if data is not None:
                materials = self._prepared(data)[0]
                for mesh in data.mesh_info:
                    values = dict(mesh.uniforms)
                    values.update(self.uniforms)
                    values.update(materials.get(mesh.material_index, ({}, {}))[0])
                    variants.add(tuple(self.shaders) + renpy.gl2.gltf.material_parts(values))
        parents = [shaders]
        if "renpy.gltf.peel_color" in shaders:
            base = tuple(part for part in shaders if part != "renpy.gltf.peel_color")
            parents.extend((base, base + ("renpy.gltf.peel_depth",)))
        for parent in parents:
            for variant in variants:
                renpy.gl2.gl2shadercache.predict_shader(parent + variant)
        return ()

    def predict_one(self):
        new_predicted.add(self)

    def __hash__(self):
        return id(self)

    def __eq__(self, other):
        return self is other


class GLTFCamera(Displayable):
    """
    :doc: assimp

    Render through a camera authored in a ``GLTFModel(..., scene=True)``.
    Use this instead of a separate perspective Transform, and wrap it in
    ``GLTFScene`` for PBR lighting. Objects and lights use the source model's
    scaled scene coordinates; no manual centering offset is needed.

    `source`
        The GLTFModel supplying camera definitions, node overrides and animation.

    `child`
        Optional scene contents. Defaults to the source model itself.

    `camera`
        Camera definition name or numeric source index. None selects the first
        camera instance in the active scene.

    `node`
        Optional camera node name/index, required when an explicitly selected
        camera definition has multiple instances. Camera pose follows its node
        hierarchy and the source model's animation. Node scale does not change
        field of view. Degenerate camera axes raise an error.

    Perspective field of view, finite/infinite far planes, and orthographic
    magnification are imported. Authored aspect ratios fit within the available
    rectangle, leaving transparent bars. Missing perspective aspect ratios use
    the offered rectangle. Recreate the camera to change saved selection.
    """

    camera = None
    node = None

    def __init__(self, source, child=None, *, camera=None, node=None, **properties):
        super().__init__(**properties)
        if not isinstance(source, GLTFModel) or not source.scene:
            raise ValueError("GLTFCamera requires a GLTFModel with scene=True.")
        if any(value is not None and type(value) not in (str, int) for value in (camera, node)):
            raise ValueError("Camera and node selection require a name or numeric index.")
        self.source = source
        self.child = source if child is None else renpy.easy.displayable(child)
        self.camera, self.node = camera, node
        self._duplicatable = self.child._duplicatable or source._duplicatable
        self._save_target = self.child._save_target or source._save_target

    def _duplicate(self, args):
        source = self.source._duplicate(args)
        child = source if self.child is self.source else self.child._duplicate(args)
        if child is self.child and source is self.source:
            return self
        rv = self._copy(args)
        rv.source, rv.child, rv._duplicatable = source, child, False
        source._unique()
        child._unique()
        return rv

    def _in_current_store(self):
        source = self.source._in_current_store()
        child = source if self.child is self.source else self.child._in_current_store()
        if source is self.source and child is self.child:
            return self
        rv = self._copy()
        rv.source, rv.child = source, child
        return rv

    def render(self, width, height, st, at):
        width, height = max(width, 1), max(height, 1)
        data = self.source.load()
        node = data.asset.camera_node(self.camera, self.node)
        definition = data.asset.cameras[data.asset.nodes[node]["camera"]]
        matrices, _visible = self.source._pose(data, st, redraw_target=self)
        view, projection, size = renpy.gl2.gltfscene.camera_matrices(
            definition, matrices[node], width, height, self.source.zoom)
        camera = Render(*size)
        camera.blit(renpy.display.render.render(self.child, *size, st, at), (0, 0))
        camera.reverse, camera.forward = view, view.inverse()
        camera.matrix_kind = renpy.gl2.gl2draw.MATRIX_KINDS[0]
        camera.add_property("depth", True)
        camera.add_uniform("u_gltf_orthographic_camera", float(definition["type"] == "orthographic"))
        projected = Render(*size)
        projected.reverse, projected.forward = projection, projection.inverse()
        projected.matrix_kind = renpy.gl2.gl2draw.MATRIX_KINDS[1]
        projected.blit(camera, (0, 0))
        viewport = Render(*size)
        viewport.xclipping = viewport.yclipping = True
        viewport.add_property("gltf_camera_viewport", True)
        viewport.blit(projected, (0, 0))
        rv = Render(width, height)
        rv.blit(viewport, ((width - size[0]) / 2, (height - size[1]) / 2))
        return rv

    def visit(self):
        return [self.child] if self.child is self.source else [self.child, self.source]

    def event(self, ev, x, y, st):
        return self.child.event(ev, x, y, st)

    def _handles_event(self, event):
        return self.child._handles_event(event)


class GLTFReflectionProbe(renpy.object.Object):
    """
    :doc: assimp

    A static equirectangular reflection map captured at `position`, with
    parallax correction inside the scene-coordinate box `box_min`, `box_max`.
    Its image accepts the same formats as :class:`GLTFScene`'s environment.
    `intensity` is nonnegative; `rotation` is in degrees about Y. `fade` is
    a nonnegative distance over which its lighting fades at the box edges.
    Up to two probes may be passed to ``GLTFScene(reflection_probes=...)``.
    Probes supply lighting and reflections, not visible geometry or backgrounds.
    """

    def __init__(self, environment, *, position, box_min, box_max, intensity=1.0, rotation=0.0, fade=0.0):
        self.position = _material_vector(position, 3, "probe position")
        self.box_min = _material_vector(box_min, 3, "probe box_min")
        self.box_max = _material_vector(box_max, 3, "probe box_max")
        if any(not lo < point < hi
                for lo, hi, point in zip(self.box_min, self.box_max, self.position)):
            raise ValueError("A reflection probe needs a nonempty box containing its capture position.")
        self.intensity = renpy.gl2.gltf.number(intensity, "probe intensity", 0)
        self.rotation = renpy.gl2.gltf.number(rotation, "probe rotation")
        self.fade = renpy.gl2.gltf.number(fade, "probe fade", 0)
        self.environment = renpy.easy.displayable(environment)
        if not isinstance(self.environment, renpy.display.im.ImageBase):
            raise ValueError("A reflection probe requires an image filename or image manipulator.")


def _environment_model(environment, quality):
    """Keep image decoding and bounded GPU filtering identical for global and local environment maps."""
    settings = {"low": (256, 64), "medium": (256, 128), "high": (512, 256)}
    if quality not in settings:
        raise ValueError("environment_quality must be low, medium, or high.")
    image = renpy.easy.displayable(environment)
    if not isinstance(image, renpy.display.im.ImageBase):
        raise ValueError("An environment requires an image filename or image manipulator.")
    source = image
    while isinstance(source, renpy.display.im.UnoptimizedTexture):
        source = source.image
    is_hdr = isinstance(source, renpy.display.im.Image) and source.filename.lower().endswith(".hdr")
    image = renpy.display.im.UnoptimizedTexture(image, premultiply=False, srgb=not is_hdr, mipmap=True)
    width, samples = settings[quality]
    model = (renpy.display.model.Model(size=(width, width * 4)).shader("renpy.gltf.environment")
        .property("drawable_resolution", False).property("mipmap", False)
        .uniform("u_gltf_filter_samples", float(samples)))
    model.textures.append(renpy.display.model.Texture(image, False, False, False,
        (renpy.uguu.GL_REPEAT, renpy.uguu.GL_CLAMP_TO_EDGE), False))
    return model


def _environment_render(model, st, at):
    """Retain GPU results on the renderer, never Render objects or native textures on saved scenes."""
    cache = renpy.display.draw.texture_loader.environment_cache
    owner = cache.owner(model)
    signature = (model.size, tuple(model.uniforms.items()), model.textures[0].displayable)
    cached = cache.get(owner, "filtered", signature)
    if cached is not None:
        return cached
    rv = Render(*model.size)
    rv.blit(renpy.display.render.render(model, *model.size, st, at), (0, 0))
    rv.add_property("gltf_target", {"hdr": True, "mipmap": False, "drawable_resolution": False})
    rv.add_property("gltf_target_store", (cache, (cache.epoch, owner, "filtered", signature)))
    return rv


class GLTFScene(Displayable):
    """
    :doc: assimp

    Lights a scene containing ``GLTFModel`` displayables with the
    ``renpy.gltf.pbr`` shader. Wrap the camera or perspective transform in
    this displayable. The scene blends in linear, half-float HDR before
    exposure, Reinhard tone mapping, and conversion to Ren'Py's sRGB output.
    Place ordinary UI outside this displayable.

    `child`
        The camera and its models. Enable ``gl_depth True`` on their shared
        camera to sort transparent meshes across models.

    `light_direction`
        A nonzero vector pointing from surfaces toward a directional light,
        in scene coordinates: X right, Y down, Z toward the viewer.

    `light_color`
        Linear RGB irradiance. Values above 1 are allowed; zero disables
        this manual directional light. Imported and explicit lights remain.

    `import_lights`
        If True (the default), uses ``KHR_lights_punctual`` lights imported
        with standard-material models in the child scene.

    `lights`
        Additional :class:`GLTFLight` displayables in scene coordinates.
        Alternatively, place lights beside the models inside the camera.
        At most eight active lights are supported, including the manual
        directional light. Muted lights do not count toward this limit.

    `meters_per_unit`
        Positive physical scale for distances. For glTF assets in meters
        loaded with ``zoom=100``, use ``0.01``. The default is 1.

    `shadows`
        If True (the default), builds depth-map shadows for active lights.
        Directional and spot lights use one tile; point lights use six.

    `shadow_size`
        Requested pixels per tile, an integer from 64 to 2048 (default 512).
        Tiles shrink when necessary to fit the renderer's texture limits.

    `shadow_bias`
        Depth bias from 0 to 0.05 (default 0.0005). Raise it to reduce
        self-shadowing artifacts; excessive bias detaches shadows.

    `shadow_cascades`, `shadow_distance`
        Directional lights use one world-bounds map by default. Select 2 or 4
        cascades for camera-frustum maps with stable texel grids and blended
        transitions. Cascades require one shared camera. `shadow_distance`
        limits their camera depth in meters (default 50); the final interval
        fades to unshadowed lighting. Point and spot lights keep their maps.

    `shadow_cache`
        Reuse unchanged shadow maps (default True). False rebuilds every
        frame, for comparison. The renderer bounds retained GPU storage
        with :var:`config.gltf_shadow_cache_size_mb`.

    `colored_shadows`
        False by default. True retains two transmitting/alpha-blended
        surfaces per light ray, including base tint, specular Fresnel and
        volume absorption. Adds two fragment samplers and an HDR/depth atlas.
        Light rays stay straight; caustics require the separate quality option.

    `caustics`
        False by default. True also enables colored shadows and moves light
        through the nearest transmitting surface to the first opaque receiver.
        A 64-cell light-space grid uses 32 ray steps and six refinements;
        brightening is capped at four times direct light. Adds one sampler.
        This is a single-interface approximation with geometric normals,
        limited by the light atlas's visible depth and face boundaries.

    `transmission`
        If True (the default), enables ``KHR_materials_transmission``,
        volume absorption, and screen-space refraction in PBR materials.
        False renders these materials using their opaque PBR response.

    `refraction_batch`
        Reuse the framebuffer copy for disjoint smooth, zero-thickness panes
        (default True). Overlapping, rough, displaced, or custom-shader panes
        retain separate copies. False provides the original path for comparison.

    `transparency_layers`
        Enable per-pixel depth peeling for intersecting transparent materials
        with an integer from 1 to 8. The default 0 sorts mesh centers. Peeling
        shades the nearest requested number of transparent surfaces against
        completed farther layers; remaining farther surfaces are omitted.
        Requires one shared camera and built-in glTF material shaders.

    `refraction_margin`
        Extra view width/height on each edge, from 0 to 0.5. The default 0
        samples the visible frame only. Positive values render an expanded
        view for nearby offscreen refraction, then crop to the original camera
        framing and coverage. Margins shrink to the GPU's texture limits.

    `depth_output`
        Retain the scene's geometry depth for :class:`GLTFDepthOfField`.
        The default False avoids its extra depth texture when not needed.

    `environment`
        An optional equirectangular sRGB image filename, image manipulator,
        or linear Radiance ``.hdr`` image filename.
        The top faces -Y, the center faces +X, and three quarters across
        faces +Z. It supplies lighting and reflections, not a background.
        Filtering is cached while the image remains unchanged.

    `environment_intensity`
        A nonnegative multiplier for environment lighting.

    `environment_rotation`
        Rotation of the environment about the scene's Y axis, in degrees.

    `environment_quality`
        ``"low"`` uses 64 samples at 256 pixels per roughness band,
        ``"medium"`` uses 128 samples at that resolution, and ``"high"``
        uses 256 samples at 512 pixels. The default is ``"low"``.

    `reflection_probes`
        A sequence of at most two :class:`GLTFReflectionProbe` objects. Probes
        fade into the global environment outside their boxes. Overlapping
        probes blend with normalized weights. Filtering and atlas storage
        share a renderer-owned 64 MiB cache, cleared with the GPU context.

    `exposure`
        A nonnegative multiplier applied to the scene before tone mapping.

    Supported on desktop only. Requires OpenGL 3.0 and half-float color attachments.
    Unsupported renderers raise an error rather than silently clipping HDR.
    """

    import_lights = False
    lights = ()
    meters_per_unit = 1.0
    shadows = False
    shadow_size = 512
    shadow_bias = 0.0005
    shadow_cascades = 1
    shadow_distance = 50.0
    colored_shadows = False
    caustics = False
    transmission = False
    shadow_cache = True
    refraction_batch = True
    transparency_layers = 0
    refraction_margin = 0.0
    depth_output = False
    environment_quality = "low"
    environment_fallback = None
    reflection_probes = ()
    reflection_models = ()

    def __init__(self, child, *, light_direction=(0, -1, 1), light_color=(3, 3, 3),
                 environment=None, environment_intensity=1.0, environment_rotation=0.0, exposure=1.0,
                 import_lights=True, lights=(), meters_per_unit=1.0, shadows=True,
                 shadow_size=512, shadow_bias=0.0005, transmission=True, shadow_cache=True,
                 refraction_batch=True, depth_output=False, environment_quality="low", reflection_probes=(),
                 transparency_layers=0, refraction_margin=0.0, shadow_cascades=1, shadow_distance=50.0,
                 colored_shadows=False, caustics=False, **properties):
        super().__init__(**properties)
        self.child = renpy.easy.displayable(child)
        self._duplicatable = self.child._duplicatable
        self._save_target = self.child._save_target
        lights = tuple(lights)
        if type(import_lights) is not bool or any(not isinstance(i, renpy.gl2.gltf.GLTFLight) for i in lights):
            raise ValueError("import_lights must be a bool and lights must contain GLTFLight objects.")
        self.import_lights = import_lights
        self.lights = lights
        self.meters_per_unit = renpy.gl2.gltf.number(meters_per_unit, "meters_per_unit", 0.000000001)
        if type(shadows) is not bool or type(shadow_size) is not int or not 64 <= shadow_size <= 2048:
            raise ValueError("shadows must be a bool and shadow_size an integer from 64 to 2048.")
        self.shadows = shadows
        self.shadow_size = shadow_size
        self.shadow_bias = renpy.gl2.gltf.number(shadow_bias, "shadow_bias", 0, 0.05)
        if type(shadow_cascades) is not int or shadow_cascades not in (1, 2, 4):
            raise ValueError("shadow_cascades must be 1, 2, or 4.")
        self.shadow_cascades = shadow_cascades
        self.shadow_distance = renpy.gl2.gltf.number(shadow_distance, "shadow_distance", 0.000001)
        renpy.gl2.gltf.number(self.shadow_distance / self.meters_per_unit, "shadow distance in scene units", 0)
        if type(colored_shadows) is not bool or type(caustics) is not bool:
            raise ValueError("colored_shadows and caustics must be bools.")
        self.caustics = caustics
        self.colored_shadows = colored_shadows or caustics
        if type(transmission) is not bool:
            raise ValueError("transmission must be a bool.")
        self.transmission = transmission
        if type(shadow_cache) is not bool:
            raise ValueError("shadow_cache must be a bool.")
        self.shadow_cache = shadow_cache
        if type(refraction_batch) is not bool:
            raise ValueError("refraction_batch must be a bool.")
        self.refraction_batch = refraction_batch
        if type(transparency_layers) is not int or not 0 <= transparency_layers <= 8:
            raise ValueError("transparency_layers must be an integer from 0 to 8.")
        self.transparency_layers = transparency_layers
        self.refraction_margin = renpy.gl2.gltf.number(refraction_margin, "refraction_margin", 0, 0.5)
        if type(depth_output) is not bool:
            raise ValueError("depth_output must be a bool.")
        self.depth_output = depth_output
        direction = _material_vector(light_direction, 3, "light_direction")
        length = math.hypot(*direction)
        if not length or not math.isfinite(length):
            raise ValueError("light_direction must be nonzero.")
        self.light_direction = tuple(i / length for i in direction)
        self.light_color = _material_vector(light_color, 3, "light_color")
        self.environment_intensity, self.environment_rotation, self.exposure = _material_vector(
            (environment_intensity, environment_rotation, exposure), 3, "environment/exposure")
        if min(*self.light_color, self.environment_intensity, self.exposure) < 0:
            raise ValueError("Light color, environment intensity, and exposure must be nonnegative.")

        self.environment_quality = environment_quality
        if environment_quality not in ("low", "medium", "high"):
            raise ValueError("environment_quality must be low, medium, or high.")
        self.environment = _environment_model(environment, environment_quality) if environment is not None else None
        self.environment_fallback = _environment_model(renpy.display.im.Null("#000"), environment_quality)
        self.reflection_probes = tuple(reflection_probes)
        if len(self.reflection_probes) > 2 or any(not isinstance(i, GLTFReflectionProbe) for i in self.reflection_probes):
            raise ValueError("reflection_probes must contain at most two GLTFReflectionProbe objects.")
        self.reflection_models = tuple(_environment_model(i.environment, environment_quality) for i in self.reflection_probes)

    def _duplicate(self, args):
        child = self.child._duplicate(args)
        if child is self.child:
            return self
        child._unique()
        rv = self._copy(args)
        rv.child = child
        rv._duplicatable = False
        return rv

    def _in_current_store(self):
        child = self.child._in_current_store()
        if child is self.child:
            return self
        rv = self._copy()
        rv.child = child
        rv._duplicatable = child._duplicatable
        return rv

    def render(self, width, height, st, at):
        if renpy.mobile or renpy.emscripten:
            raise RuntimeError("GLTFScene lighting, shadows, and refraction are supported only on desktop.")
        profiler = getattr(renpy.display.draw, "gltf_profile", None)
        if profiler is not None:
            started = time.perf_counter()
        child = renpy.display.render.render(self.child, width, height, st, at)
        original_size = child.get_size()
        coverage, region = None, (0, 0, 1, 1)
        if self.refraction_margin:
            child, coverage, region = renpy.gl2.gltf.refraction_guard(child, self.refraction_margin)
        width, height = child.get_size()
        meshes, lights = renpy.gl2.gltf.collect_scene(child, width, height, self.import_lights)
        if coverage is not None and any(not record[5].get("gltf_camera_projection", False) for record in meshes):
            raise ValueError("Refraction margins require a camera projection transform.")
        if coverage is not None and len({(renpy.gl2.gltf.matrix_values(r[2]), renpy.gl2.gltf.matrix_values(r[3])) for r in meshes}) > 1:
            raise ValueError("Refraction margins require one shared camera view and projection.")
        lights.extend((light, light.position, light.direction) for light in self.lights)
        if any(self.light_color):
            light = renpy.gl2.gltf.GLTFLight("directional", direction=tuple(-i for i in self.light_direction),
                color=self.light_color, name="_directional")
            lights.insert(0, (light, light.position, light.direction))
        lights = [entry for entry in lights if entry[0].intensity and any(entry[0].color)]
        scene = Render(width, height)
        if coverage is not None:
            scene.add_property("gltf_pixel_resolution", True)
            scene.add_property("gltf_target", {"hdr": True, "depth_texture": True,
                "gltf_depth_required": True, "mipmap": False, "drawable_resolution": False})
        scene.add_property("gltf_refraction_batch", self.refraction_batch)
        scene.blit(child, (0, 0))
        scene.add_uniform("u_gltf_light_direction", self.light_direction)
        scene.add_uniform("u_gltf_linear", 1.0)
        scene.add_uniform("u_gltf_light_color", self.light_color)
        if self.shadow_cascades > 1:
            scene.add_shader("renpy.gltf.cascades")
        for name, value in renpy.gl2.gltf.light_uniforms(lights, self.meters_per_unit).items():
            scene.add_uniform(name, value)
        shadow_plan = {} if self.colored_shadows and self.shadows and self.transmission else None
        shadow_atlas, shadow_uniforms = renpy.gl2.gltf.shadow_uniforms(
            meshes, lights, self.shadow_size, self.shadow_bias, self.meters_per_unit, self.shadows, self.transmission,
            renpy.display.draw.texture_loader.shadow_cache if self.shadow_cache else None, self,
            cascades=self.shadow_cascades, distance=self.shadow_distance, plan=shadow_plan)
        scene.add_uniform("u_gltf_shadow", shadow_atlas)
        for name, value in shadow_uniforms.items():
            scene.add_uniform(name, value)
        scene.add_property("texture_wrap_gltf_shadow", (renpy.uguu.GL_CLAMP_TO_EDGE, renpy.uguu.GL_CLAMP_TO_EDGE))
        scene.add_property("texture_scaling_gltf_shadow", (renpy.uguu.GL_NEAREST, renpy.uguu.GL_NEAREST))
        if shadow_plan is not None:
            glass = renpy.gl2.gltf.glass_shadows(meshes, shadow_plan, shadow_atlas, self.meters_per_unit,
                renpy.display.draw.texture_loader.shadow_cache if self.shadow_cache else None, self)
            scene.add_shader("renpy.gltf.colored_shadows")
            scene.add_uniform("u_gltf_glass_color", glass)
            scene.add_uniform("u_gltf_glass_depth", glass)
            scene.add_property("texture_scaling_gltf_glass_color", (renpy.uguu.GL_NEAREST, renpy.uguu.GL_NEAREST))
            scene.add_property("texture_scaling_gltf_glass_depth", (renpy.uguu.GL_NEAREST, renpy.uguu.GL_NEAREST))
            if self.caustics:
                caustic = renpy.gl2.gltf.caustic_shadows(meshes, shadow_plan, shadow_atlas, glass, self.meters_per_unit,
                    renpy.display.draw.texture_loader.shadow_cache if self.shadow_cache else None, self)
                scene.add_shader("renpy.gltf.caustics")
                scene.add_uniform("u_gltf_caustics", caustic)
                scene.add_property("texture_scaling_gltf_caustics", (renpy.uguu.GL_LINEAR, renpy.uguu.GL_LINEAR))
        refractive = self.transmission and any(record[5].get("gltf_transmission", False) for record in meshes)
        scene.add_uniform("u_gltf_transmission_enabled", float(refractive))
        if refractive and not self.transparency_layers:
            background = Render(width, height)
            background.add_property("depth", True)
            background.add_property("gltf_target", {"hdr": True, "depth_texture": True,
                "gltf_depth_required": True, "mipmap": True, "drawable_resolution": coverage is None})
        else:
            background = render_for_texture(
                renpy.display.im.UnoptimizedTexture(renpy.display.im.Null("#0000"), premultiply=False), 1, 1, st, at)
        scene.add_uniform("u_gltf_background", background)
        scene.add_uniform("u_gltf_background_depth", background)
        scene.add_property("texture_wrap_gltf_background", (renpy.uguu.GL_CLAMP_TO_EDGE, renpy.uguu.GL_CLAMP_TO_EDGE))
        scene.add_property("texture_wrap_gltf_background_depth", (renpy.uguu.GL_CLAMP_TO_EDGE, renpy.uguu.GL_CLAMP_TO_EDGE))
        scene.add_property("texture_scaling_gltf_background_depth", (renpy.uguu.GL_NEAREST, renpy.uguu.GL_NEAREST))
        scene.add_uniform("u_gltf_environment_intensity", self.environment_intensity if self.environment or self.reflection_probes else 0.0)
        scene.add_uniform("u_gltf_environment_rotation", math.radians(self.environment_rotation))
        scene.add_property("hdr", True)
        scene.add_property("mipmap", False)
        scene.add_property("drawable_resolution", False)
        scene.add_property("texture_wrap_tex_environment", (renpy.uguu.GL_CLAMP_TO_EDGE, renpy.uguu.GL_CLAMP_TO_EDGE))
        scene.add_property("texture_scaling_tex_environment", (renpy.uguu.GL_LINEAR, renpy.uguu.GL_LINEAR))
        if self.environment is not None or self.reflection_probes or any("renpy.gltf.sheen" in record[0].shaders for record in meshes):
            model = self.environment or self.environment_fallback or _environment_model(renpy.display.im.Null("#000"), self.environment_quality)
            maps = [_environment_render(i, st, at) for i in (model,) + self.reflection_models]
            environment = maps[0]
            if self.reflection_probes:
                cache = renpy.display.draw.texture_loader.environment_cache
                owner = cache.owner(self)
                signature = (cache.owner(model), tuple(cache.owner(i) for i in self.reflection_models), self.environment_quality)
                environment = cache.get(owner, "atlas", signature)
                if environment is None:
                    environment = Render(model.size[0] * len(maps), model.size[1])
                    for i, image in enumerate(maps):
                        environment.blit(image, (model.size[0] * i, 0))
                    environment.add_property("gltf_target", {"hdr": True, "mipmap": False, "drawable_resolution": False})
                    environment.add_property("gltf_target_store", (cache, (cache.epoch, owner, "atlas", signature)))
        else:
            environment = render_for_texture(
                renpy.display.im.UnoptimizedTexture(renpy.display.im.Null("#000"), premultiply=False), 1, 1, st, at)
        scene.add_uniform("u_tex_environment", environment)
        scene.add_uniform("u_gltf_environment_pages", float(1 + len(self.reflection_probes)))
        scene.add_uniform("u_gltf_probe_count", float(len(self.reflection_probes)))
        probes = self.reflection_probes + (None,) * (2 - len(self.reflection_probes))
        scene.add_uniform("u_gltf_probe_min", tuple((*p.box_min, p.fade) if p else (0, 0, 0, 0) for p in probes))
        scene.add_uniform("u_gltf_probe_max", tuple((*p.box_max, p.intensity) if p else (0, 0, 0, 0) for p in probes))
        scene.add_uniform("u_gltf_probe_origin", tuple((*p.position, math.radians(p.rotation)) if p else (0, 0, 0, 0) for p in probes))

        if self.transparency_layers and any(record[5].get("transparent", False) for record in meshes):
            scene = renpy.gl2.gltf.peeled_scene(scene, meshes, self.transparency_layers, refractive)

        rv = Render(*original_size)
        rv.blit(scene, (0, 0))
        rv.mesh = True
        rv.add_shader("renpy.gltf.output")
        if coverage is not None:
            rv.add_shader("renpy.gltf.guard")
            rv.add_uniform("u_gltf_output_mask", coverage)
            rv.add_uniform("u_gltf_output_region", region)
            rv.add_property("texture_scaling_gltf_output_mask", (renpy.uguu.GL_NEAREST, renpy.uguu.GL_NEAREST))
        rv.add_uniform("u_gltf_exposure", self.exposure)
        rv.add_property("hdr", True)
        rv.add_property("mipmap", False)
        if refractive:
            rv.add_property("depth_texture", True)
            rv.add_property("gltf_depth_required", True)
            # One drawable-resolution framebuffer also supplies the per-layer refraction copies.
            rv.add_property("mesh_oversample_scale", 0.0)
        rv.add_property("gltf_scene", True)
        if self.depth_output:
            projection = meshes[0][3] if meshes else renpy.display.matrix.Matrix.texture_projection(width, height)
            if any(renpy.gl2.gltf.matrix_values(record[3]) != renpy.gl2.gltf.matrix_values(projection) for record in meshes):
                raise ValueError("GLTFScene depth output requires one shared camera projection.")
            scene.add_property("gltf_target", {"hdr": True, "depth_texture": True,
                "gltf_depth_required": True, "mipmap": False, "drawable_resolution": coverage is None})
            rv.add_property("gltf_depth_source", scene)
            rv.add_property("gltf_depth_projection", projection)
            rv.add_property("gltf_depth_scale", self.meters_per_unit)
        if profiler is not None:
            profiler.record("scene_prepare", time.perf_counter() - started)
        return rv

    def visit(self):
        return [self.child] + ([self.environment] if self.environment is not None else
            [self.environment_fallback] if self.environment_fallback is not None else []) + list(self.reflection_models)

    def event(self, ev, x, y, st):
        return self.child.event(ev, x, y, st)

    def _handles_event(self, event):
        return self.child._handles_event(event)

    def predict_shaders(self, shaders):
        output = ("renpy.gltf.output",) + (("renpy.gltf.guard",) if self.refraction_margin else ())
        renpy.gl2.gl2shadercache.predict_shader(shaders + output)
        inherited = ("renpy.gltf.cascades",) if self.shadow_cascades > 1 else ()
        if self.colored_shadows and self.shadows and self.transmission:
            inherited += ("renpy.gltf.colored_shadows",)
            renpy.gl2.gl2shadercache.predict_shader(("renpy.gltf.shadow", "renpy.gltf.shadow_glass"))
            renpy.gl2.gl2shadercache.predict_shader(("renpy.gltf.depth_copy",))
            if self.caustics:
                inherited += ("renpy.gltf.caustics",)
                renpy.gl2.gl2shadercache.predict_shader(("renpy.gltf.shadow", "renpy.gltf.shadow_glass", "renpy.gltf.caustic_data"))
                renpy.gl2.gl2shadercache.predict_shader(("renpy.gltf.caustic_scatter",))
        if self.transparency_layers:
            renpy.gl2.gl2shadercache.predict_shader(("renpy.gltf.depth_copy",))
            inherited += ("renpy.gltf.peel_color",)
        if self.refraction_margin:
            renpy.gl2.gl2shadercache.predict_shader(("renpy.gltf.coverage",))
        return inherited


class GLTFDepthOfField(Displayable):
    """
    :doc: assimp

    Depth-aware blur of a :class:`GLTFScene` constructed with
    ``depth_output=True``. Filtering takes place in linear HDR before
    exposure and tone mapping. Place UI outside this displayable.

    `child`
        The scene, or an image name resolving to that scene. All scene
        geometry must use one shared camera projection.

    `focus_distance`
        Positive distance from the camera in meters, using the scene's
        ``meters_per_unit``. The default is 5 meters.

    `focus_range`
        Positive distance in meters over which blur reaches ``max_blur``.
        The default is 1 meter.

    `max_blur`
        Maximum blur radius in drawable pixels, from 0 to 32 (default 8).
        Zero reproduces the ordinary scene output. The 16-sample gather
        rejects closer surfaces at depth discontinuities; it approximates
        defocus and does not trace a physical camera aperture.
    """

    focus_distance = 5.0
    focus_range = 1.0
    max_blur = 8.0

    def __init__(self, child, *, focus_distance=5.0, focus_range=1.0, max_blur=8.0, **properties):
        super().__init__(**properties)
        self.child = renpy.easy.displayable(child)
        self._duplicatable, self._save_target = self.child._duplicatable, self.child._save_target
        self.focus_distance = renpy.gl2.gltf.number(focus_distance, "focus_distance", 0.000001)
        self.focus_range = renpy.gl2.gltf.number(focus_range, "focus_range", 0.000001)
        self.max_blur = renpy.gl2.gltf.number(max_blur, "max_blur", 0, 32)

    _duplicate = GLTFScene._duplicate
    _in_current_store = GLTFScene._in_current_store
    event = GLTFScene.event
    _handles_event = GLTFScene._handles_event

    def render(self, width, height, st, at):
        # Image references wrap their target's render, hiding the scene's depth properties.
        source = renpy.display.render.render(self.child._target(), width, height, st, at)
        properties = source.properties or {}
        scene = properties.get("gltf_depth_source")
        if scene is None:
            raise ValueError("GLTFDepthOfField requires a GLTFScene with depth_output=True.")
        projection = properties["gltf_depth_projection"]
        rv = Render(*source.get_size())
        rv.depends_on(source)
        rv.blit(scene, (0, 0))
        rv.mesh = True
        rv.add_shader("renpy.gltf.dof")
        if "u_gltf_output_region" in source.uniforms:
            rv.add_shader("renpy.gltf.guard")
            rv.add_uniform("u_gltf_output_mask", source.uniforms["u_gltf_output_mask"])
            rv.add_uniform("u_gltf_output_region", source.uniforms["u_gltf_output_region"])
            rv.add_property("texture_scaling_gltf_output_mask", (renpy.uguu.GL_NEAREST, renpy.uguu.GL_NEAREST))
        rv.add_uniform("u_gltf_scene_depth", scene)
        rv.add_uniform("u_gltf_dof_inverse", projection.inverse())
        rv.add_uniform("u_gltf_dof_perspective", float(abs(projection.wdz) > 0.000001))
        rv.add_uniform("u_gltf_dof_scale", properties["gltf_depth_scale"])
        rv.add_uniform("u_gltf_dof_focus", (self.focus_distance, self.focus_range, self.max_blur))
        rv.add_uniform("u_gltf_exposure", source.uniforms["u_gltf_exposure"])
        rv.add_property("texture_wrap_gltf_scene_depth", (renpy.uguu.GL_CLAMP_TO_EDGE, renpy.uguu.GL_CLAMP_TO_EDGE))
        rv.add_property("texture_scaling_gltf_scene_depth", (renpy.uguu.GL_NEAREST, renpy.uguu.GL_NEAREST))
        rv.add_property("gltf_pass", "depth_of_field")
        rv.add_property("gltf_scene", True)
        return rv

    def visit(self):
        return [self.child]

    def predict_shaders(self, shaders):
        renpy.gl2.gl2shadercache.predict_shader(shaders + ("renpy.gltf.dof",))
        child = self.child._target()
        if getattr(child, "refraction_margin", 0):
            renpy.gl2.gl2shadercache.predict_shader(shaders + ("renpy.gltf.dof", "renpy.gltf.guard"))
        return ()


def finish_predict():
    """
    Called to finish the prediction process.
    """

    global predicted
    global new_predicted

    if new_predicted != predicted:
        predicted = new_predicted
        new_predicted = set()

        renpy.display.im.cache.start_prediction()


def preload():
    """
    Called to preload predicted models.
    """

    if predicted is None:
        return

    predicted_keys = { i.cache_key for i in predicted }

    with cache_lock:
        budget = max(0, renpy.config.gltf_model_cache_size_mb) * 1024 * 1024
        inactive = [i for i in cache if i not in predicted_keys]
        retained = sum(cache[i].memory_size for i in inactive)
        for i in inactive:
            if budget and retained <= budget:
                break
            retained -= cache[i].memory_size
            del cache[i]

    for i in predicted:
        i.load()


cdef public SDL_IOStream *assimp_load(const char *filename) nogil:
    """
    Loads the model from the given filename.
    """

    with gil:

        fn = filename.decode()

        try:
            if loader.import_data is not None and fn.replace("\\", "/") == loader.filename.replace("\\", "/"):
                import io
                f = io.BytesIO(loader.import_data)
            else:
                f = renpy.loader.load(fn)
            return open_io(f).take()
        except Exception as e:
            renpy.display.log.write("Unable to load Assimp file %r: %s", fn, e)

    return NULL
