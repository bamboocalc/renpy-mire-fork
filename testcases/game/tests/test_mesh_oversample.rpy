# Tests the texture size of a mesh drawn under a perspective camera.

# ==============
# == Fixtures ==
# ==============

init python:

    def mesh_oversample__ratio(zoom):
        """
        Draws a 100x100 mesh at `zoom` under a perspective camera, and returns the width of its texture divided by
        the drawable width it covers at 1:1.
        """

        mesh = Transform(Solid("#fff", xysize=(100, 100)), mesh=True)
        camera = Transform(Fixed(Transform(mesh, zoom=zoom), xysize=(400, 300)), xysize=(400, 300), perspective=True)

        root = renpy.display.render.render(camera, 400, 300, 0, 0)
        renpy.display.draw.screenshot(root)

        renders = [ root ]

        while renders:
            r = renders.pop()

            if r.mesh and r.cached_model is not None:
                return r.children[0][0].cached_texture.texture_width / (100 * renpy.display.draw.draw_per_virt)

            renders.extend(c[0] for c in r.children if isinstance(c[0], renpy.display.render.Render))

# ===========
# == Tests ==
# ===========

testsuite mesh_oversample:

    testcase follows_the_zoom_under_a_perspective_camera:
        description "A mesh under a perspective camera was oversampled to config.mesh_oversample, as the estimate skipped the divide by w."

        assert eval round(mesh_oversample__ratio(1.0), 1) == 1.0
        assert eval round(mesh_oversample__ratio(2.0), 1) == 2.0
