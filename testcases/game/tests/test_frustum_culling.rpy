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

# Compares actual camera output with and without imported-mesh culling.

init python:

    def frustum_culling__pixels(enabled):
        """A transformed visible box and an off-camera box share their imported geometry."""
        model = GLTFModel("Box.glb", shader="renpy.texture", zoom=100.0, frustum_cull=enabled)
        visible = Transform(model, xpos=200, ypos=150, matrixtransform=RotateMatrix(25, 40, 0))
        outside = Transform(model, xpos=20000, ypos=150)
        camera = Transform(Fixed(visible, outside, xysize=(400, 300)), xysize=(400, 300), perspective=True)
        surface = renpy.render_to_surface(camera, 400, 300, resize=True)
        return [tuple(surface.get_at((x, y))) for y in range(0, 300, 3) for x in range(0, 400, 3)]

testsuite frustum_culling:

    testcase camera_output_is_unchanged:
        $ _cull_reference = frustum_culling__pixels(False)
        $ _cull_before = renpy.gl2.gl2draw.model_stats["culled"]
        assert eval frustum_culling__pixels(True) == _cull_reference
        if eval config.debug_image_cache:
            assert eval renpy.gl2.gl2draw.model_stats["culled"] > _cull_before
