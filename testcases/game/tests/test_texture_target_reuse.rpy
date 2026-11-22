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
# Tests that a render target which died this frame lends its storage, and nothing else, to its replacement.

init python:

    def target_reuse__make(color=None, size=64, mipmap=False):
        """Renders an HDR target with a depth texture, with a depth-writing left half when given a color."""
        render = renpy.display.render.Render(size, size)
        render.add_property("depth", True)
        if color:
            render.blit(renpy.render(Solid(color, xysize=(size // 2, size)), size, size, 0, 0), (0, 0))
        return renpy.display.draw.texture_loader.render_to_texture(render,
            {"hdr": True, "depth_texture": True, "mipmap": mipmap, "drawable_resolution": False})

    def target_reuse__sample(texture, zoom=1.0, x=0.25):
        """Draws a target at the given zoom and samples a pixel across its width."""
        size = int(texture.width * zoom)
        frame = renpy.display.render.Render(size, size)
        frame.forward = renpy.display.render.Matrix2D(1.0 / zoom, 0, 0, 1.0 / zoom)
        frame.reverse = renpy.display.render.Matrix2D(zoom, 0, 0, zoom)
        frame.blit(texture, (0, 0))
        surface = renpy.display.draw.screenshot(frame)
        # Live renders keep their children until the frame's sweep, which hasn't run yet.
        frame.children.clear()
        w, h = surface.get_size()
        return tuple(surface.get_at((int(w * x), h // 2)))

    def target_reuse__redraw(mipmap):
        """Replaces a dead red target with an empty one before the frame's cleanup."""
        loader = renpy.display.draw.texture_loader
        loader.cleanup()
        old = target_reuse__make("#f00", mipmap=mipmap)
        red = target_reuse__sample(old)[:3]
        near = target_reuse__sample(old.depth_texture)[0] < 255
        numbers = old.number, old.depth_texture.number
        size = loader.get_texture_size()
        old = None
        new = target_reuse__make(mipmap=mipmap)
        return dict(red=red, near=near,
            reused=(new.number, new.depth_texture.number) == numbers,
            same_size=loader.get_texture_size() == size,
            color=target_reuse__sample(new), shrunk=target_reuse__sample(new, 0.125),
            depth=target_reuse__sample(new.depth_texture)[0])

    def target_reuse__sampler():
        """Compares a magnified edge after the dead target was sampled nearest with one from fresh storage."""
        loader = renpy.display.draw.texture_loader
        loader.cleanup()
        fresh = target_reuse__make("#f00", size=8)
        expected = [target_reuse__sample(fresh, 20.0, x / 40.0) for x in range(16, 24)]
        fresh.properties["texture_scaling"] = "nearest"
        nearest = [target_reuse__sample(fresh, 20.0, x / 40.0) for x in range(16, 24)]
        fresh = None
        reused = target_reuse__make("#f00", size=8)
        return expected != nearest, [target_reuse__sample(reused, 20.0, x / 40.0) for x in range(16, 24)] == expected

    def target_reuse__mismatch(case):
        """Returns whether an unsuitable target lent its storage."""
        loader = renpy.display.draw.texture_loader
        loader.cleanup()
        old = target_reuse__make("#f00")
        numbers = old.number, old.depth_texture.number
        size, mipmap = 64, False
        if case == "alive":
            new = target_reuse__make()
            return bool({new.number, new.depth_texture.number} & set(numbers))
        elif case == "size":
            size = 32
        elif case == "mip_layout":
            mipmap = True
        elif case == "added_mipmap":
            old.add_mipmap()
        old = None
        new = target_reuse__make(size=size, mipmap=mipmap)
        return new.number == numbers[0]

    def target_reuse__released():
        """Returns whether cleanup still frees a target that nothing reused."""
        loader = renpy.display.draw.texture_loader
        loader.cleanup()
        before = loader.get_texture_size()
        target = target_reuse__make("#f00")
        target = None
        loader.cleanup()
        return loader.get_texture_size() == before

testsuite texture_target_reuse:

    testcase redraw_reuses_cleared_storage:
        description "A changing glTF scene allocated and deleted its HDR and depth targets on every frame."
        parameter mipmap = [False, True]

        $ _reuse = target_reuse__redraw(mipmap)
        assert eval _reuse["red"][0] > 200 and _reuse["near"]
        assert eval _reuse["color"][:3] == (0, 0, 0) and _reuse["shrunk"][:3] == (0, 0, 0)
        assert eval _reuse["depth"] == 255
        assert eval _reuse["same_size"]
        assert eval _reuse["reused"]

    testcase sampler_state_starts_fresh:
        $ _reuse_changed, _reuse_reset = target_reuse__sampler()
        assert eval _reuse_changed
        assert eval _reuse_reset

    testcase unsuitable_storage_is_left_alone:
        parameter case = ["alive", "size", "mip_layout", "added_mipmap"]

        assert eval not target_reuse__mismatch(case)

    testcase unused_storage_is_released:
        assert eval target_reuse__released()
