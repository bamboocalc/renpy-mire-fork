# Tests that depth testing still works after a pass whose top-level render had gl_depth.

# ==============
# == Fixtures ==
# ==============

init python:

    def gl_depth_reset__center():
        """
        Renders a gl_depth camera whose far blue plane is drawn after its near red plane, as the top-level
        render, and returns which plane shows at its center.
        """

        near = Transform(Solid("#f00", xysize=(200, 200)), align=(0.5, 0.5), zpos=100)
        far = Transform(Solid("#00f", xysize=(200, 200)), align=(0.5, 0.5), zpos=-100)
        camera = Transform(Fixed(near, far, xysize=(400, 300)), xysize=(400, 300), perspective=True, gl_depth=True)

        surface = renpy.render_to_surface(camera, 400, 300, resize=True)
        r, g, b, a = surface.get_at((200, 150))
        return "near" if r > b else "far"

# ===========
# == Tests ==
# ===========

testsuite gl_depth_reset:

    testcase near_plane_wins_every_pass:
        description "The root drawing context kept has_depth from a top-level gl_depth render, so later passes skipped the depth test."

        assert eval gl_depth_reset__center() == "near"
        assert eval gl_depth_reset__center() == "near"
