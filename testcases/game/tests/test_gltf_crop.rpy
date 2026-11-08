# Tests that a crop above a perspective camera clips the 3D content it contains.

# ==============
# == Fixtures ==
# ==============

init python:

    def gltf_crop__camera():
        """
        A 400x300 perspective camera with the test box turned in its center.
        """

        box = Transform("GLTFBox", xpos=200, ypos=150, matrixtransform=RotateMatrix(25, 40, 0))
        return Transform(Fixed(box, xysize=(400, 300)), xysize=(400, 300), perspective=True)

    def gltf_crop__pixels(crop):
        """
        Renders the camera cropped to `crop` at that place in a 400x300 frame, and returns the number of visible
        pixels inside and outside the crop rectangle.
        """

        x, y, w, h = crop
        frame = Fixed(Transform(gltf_crop__camera(), crop=crop, xpos=x, ypos=y), xysize=(400, 300))
        surface = renpy.render_to_surface(frame, 400, 300, resize=True)

        inside = outside = 0

        for py in range(surface.get_height()):
            for px in range(surface.get_width()):
                if surface.get_at((px, py))[3]:
                    if x <= px < x + w and y <= py < y + h:
                        inside += 1
                    else:
                        outside += 1

        return inside, outside

# ===========
# == Tests ==
# ===========

testsuite gltf_crop:

    testcase draws_only_inside_the_crop:
        description "A crop above a perspective camera drew nothing, as its clip polygon went through the projection."

        assert eval gltf_crop__pixels((0, 0, 400, 300))[0] > 0
        assert eval gltf_crop__pixels((150, 100, 100, 100))[0] > 0
        assert eval gltf_crop__pixels((150, 100, 100, 100))[1] == 0
        assert eval gltf_crop__pixels((200, 150, 120, 90))[1] == 0
