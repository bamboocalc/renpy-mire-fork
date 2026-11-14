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

import pickle

import pytest

from renpy.display.matrix import Matrix
from renpy.display.render import IDENTITY
from renpy.gl2.assimp import GLTFModel
from renpy.gl2.gl2mesh import SOLID_LAYOUT
from renpy.gl2.gl2mesh3 import Mesh3


@pytest.mark.parametrize("axis", range(3))
@pytest.mark.parametrize("distance, visible", [(0, True), (1.5, True), (2, False), (-2, False)])
def test_bounds_against_all_six_clip_planes(axis, distance, visible):
    """Bounds touching a clip plane survive; wholly outside bounds are rejected on every axis."""

    mesh = Mesh3(SOLID_LAYOUT, 0, 0)
    mesh.bounds = (-0.5, -0.5, -0.5, 0.5, 0.5, 0.5)
    offset = [0, 0, 0]
    offset[axis] = distance
    assert mesh.is_visible(Matrix.offset(*offset)) is visible


def test_large_bounds_enclosing_the_camera_are_visible():
    """Testing only whether individual corners are inside would incorrectly hide enclosing rooms."""

    mesh = Mesh3(SOLID_LAYOUT, 0, 0)
    mesh.bounds = (-100, -100, -100, 100, 100, 100)
    assert mesh.is_visible(IDENTITY)
    assert mesh.is_visible(Matrix.scale(-2, 3, 1) * Matrix.rotate(15, 40, 20))


def test_unknown_and_nonfinite_bounds_are_not_rejected():
    """Meshes without imported bounds and numerically invalid transforms remain conservative."""

    mesh = Mesh3(SOLID_LAYOUT, 0, 0)
    assert mesh.is_visible(IDENTITY)
    mesh.bounds = (-1, -1, -1, 1, 1, 1)
    assert mesh.is_visible(Matrix.scale(float("nan"), 1, 1))


def test_old_saved_models_default_to_no_culling():
    """Old displayables have no saved culling attribute and retain vertex-displacing shader behavior."""

    model = GLTFModel("unread.glb", shader="renpy.texture", frustum_cull=True)
    del model.__dict__["frustum_cull"]
    restored = pickle.loads(pickle.dumps(model))
    assert restored.frustum_cull is False
    assert restored.filename == "unread.glb"


@pytest.mark.parametrize("near, far, visible", [(2, 3, True), (-3, -2, False), (-2, 2, True)])
def test_bounds_in_front_of_behind_and_across_the_eye(near, far, visible):
    """Homogeneous clipping rejects bounds behind the eye without dividing across w=0."""

    transform = Matrix([1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1])
    transform.wdz = 1
    transform.wdw = 0
    transform.zdz = 0
    mesh = Mesh3(SOLID_LAYOUT, 0, 0)
    mesh.bounds = (-0.5, -0.5, near, 0.5, 0.5, far)
    assert mesh.is_visible(transform) is visible
