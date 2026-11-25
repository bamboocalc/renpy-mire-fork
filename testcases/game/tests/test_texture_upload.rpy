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

# Tests cooperative uploads and finishing an upload when a texture becomes visible.

init python:

    renpy.register_shader("texture_upload.uniform", variables="""
        uniform sampler2D u_upload_texture;
        in vec2 a_tex_coord;
        out vec2 v_upload_texcoord;
    """, vertex_200="""
        v_upload_texcoord = a_tex_coord;
    """, fragment_200="""
        fragment_color = texture(u_upload_texture, v_upload_texcoord);
    """)

    def texture_upload__make(premultiplied=True):
        """Uses an odd-width subsurface so row pitch and the final short chunk are exercised."""
        import renpy.pygame as pygame
        surface = pygame.Surface((2050, 1026), pygame.SRCALPHA, 32)
        surface.fill((64, 0, 0, 128))
        surface.fill((0, 128, 0, 128), (0, 511, 2050, 3))
        surface.fill((0, 0, 255, 255), (0, 1023, 2050, 2))
        surface = surface.subsurface((1, 0, 2049, 1025))
        return renpy.display.draw.load_texture(surface, properties={"premultiplied": premultiplied, "mipmap": True})

    def texture_upload__samples(texture):
        """Hashes the complete GPU output and samples both sides of the color bands."""
        import hashlib
        import io
        surface = renpy.display.draw.screenshot(texture)
        w, h = surface.get_size()
        stream = io.BytesIO()
        renpy.pygame.image.save(surface, stream, "upload.png", compression=0)
        samples = [tuple(surface.get_at((w // 2, min(h - 1, int(y * h / 1025)))))
                   for y in (0, 510, 511, 513, 514, 1023, 1024)]
        return hashlib.sha256(stream.getvalue()).hexdigest(), samples

    def texture_upload__compare_pbo(chunk_size, premultiplied):
        """Compare complete frames from direct and pixel unpack buffer uploads with the same source pitch."""
        previous_size, previous_pbo = config.texture_upload_chunk_size, config.texture_upload_use_pbo
        config.texture_upload_chunk_size = chunk_size
        try:
            config.texture_upload_use_pbo = True
            texture = texture_upload__make(premultiplied)
            texture.load()
            buffered = texture_upload__samples(texture)
            config.texture_upload_use_pbo = False
            texture = texture_upload__make(premultiplied)
            texture.load()
            direct = texture_upload__samples(texture)
            return direct == buffered and direct[1][0] != direct[1][-1]
        finally:
            config.texture_upload_chunk_size = previous_size
            config.texture_upload_use_pbo = previous_pbo

    def texture_upload__begin():
        """Captures the first step before the interaction loop can finish queued work."""
        texture = texture_upload__make()
        texture.load(partial=True)
        return texture, texture.loaded

    def texture_upload__cancel_delta():
        """Drops a locally held texture while the upload queue holds only a weak reference."""
        loader = renpy.display.draw.texture_loader
        before = loader.get_texture_size()[0]
        texture = texture_upload__make()
        texture.load(partial=True)
        texture = None
        return loader.get_texture_size()[0] - before

    def texture_upload__uniform_frame():
        """Draws a still-pending uniform texture without placing that texture among the children."""
        texture = texture_upload__make()
        texture.load(partial=True)
        frame = renpy.display.render.Render(texture.width, texture.height)
        frame.mesh = texture.mesh
        frame.add_shader("texture_upload.uniform")
        frame.add_uniform("u_upload_texture", texture)
        surface = renpy.display.draw.screenshot(frame)
        return texture.loaded, surface.get_at((surface.get_width() // 2, surface.get_height() - 1))[2]

    def texture_upload__prediction_pause():
        """Checks that a static prediction pause keeps processing a texture's remaining GPU steps."""
        texture = texture_upload__make()
        texture.load(partial=True)
        renpy.pause(predict=True)
        return texture.loaded

label texture_upload_prediction_fixture:
    $ _upload_prediction_done = False
    $ _upload_prediction_done = texture_upload__prediction_pause()
    "Prediction finished."
    return

testsuite texture_upload:

    # Uploads finish in one step at the default chunk size of 0, which would leave nothing partial to test.
    before testcase:
        $ _upload_chunk_size, config.texture_upload_chunk_size = config.texture_upload_chunk_size, 256 * 1024

    after testcase:
        $ config.texture_upload_chunk_size = _upload_chunk_size

    testcase direct_upload_matches_buffered_upload:
        parameter chunk_size = [0, 256 * 1024]
        parameter premultiplied = [True, False]
        assert eval texture_upload__compare_pbo(chunk_size, premultiplied)

    testcase demand_finishes_a_partial_upload:
        $ _upload_texture, _upload_loaded = texture_upload__begin()
        assert eval not _upload_loaded
        $ _upload_texture.load()
        assert eval _upload_texture.loaded
        $ _upload_samples = texture_upload__samples(_upload_texture)
        assert eval _upload_samples[1][0] != _upload_samples[1][-1]
        $ _upload_reference = texture_upload__make()
        $ _upload_reference.load()
        assert eval texture_upload__samples(_upload_reference) == _upload_samples
        $ _upload_texture = _upload_reference = None

    testcase cooperative_steps_complete:
        $ _upload_texture, _upload_loaded = texture_upload__begin()
        assert eval not _upload_loaded
        pause 0.01 until eval _upload_texture.loaded timeout 5.0
        $ _upload_reference = texture_upload__make()
        $ _upload_reference.load()
        assert eval texture_upload__samples(_upload_texture) == texture_upload__samples(_upload_reference)
        $ _upload_texture = _upload_reference = None

    testcase cancellation_releases_gpu_accounting:
        assert eval texture_upload__cancel_delta() == 0

    testcase uniform_textures_finish_before_the_mesh_draws:
        $ _upload_uniform_loaded, _upload_uniform_blue = texture_upload__uniform_frame()
        assert eval _upload_uniform_loaded
        assert eval _upload_uniform_blue == 255

    testcase static_prediction_pause_finishes_uploads:
        run Jump("texture_upload_prediction_fixture")
        pause 0.01 until eval _upload_prediction_done timeout 10.0
        assert eval _upload_prediction_done


init python:

    def texture_reset__make(color):
        """Loads a small opaque texture through the fast upload program."""
        import renpy.pygame as pygame
        surface = pygame.Surface((64, 64), pygame.SRCALPHA, 32)
        surface.fill(color)
        texture = renpy.display.draw.load_texture(surface)
        texture.load()
        return texture

testsuite texture_context_reset:

    testcase survivors_release_nothing_after_reset:
        description "A texture and upload program from before a GL context reset free nothing that the new context reuses."

        $ _reset_old = texture_reset__make((255, 0, 0, 255))
        $ renpy.display_reset()
        pause 0.2
        $ _reset_new = texture_reset__make((0, 0, 255, 255))
        $ _reset_size = renpy.display.draw.texture_loader.get_texture_size()
        $ _reset_old = None
        $ renpy.display.draw.texture_loader.cleanup()
        assert eval renpy.display.draw.texture_loader.get_texture_size() == _reset_size
        $ _reset_pixel = tuple(renpy.display.draw.screenshot(_reset_new).get_at((32, 32)))
        $ _reset_new = None
        assert eval _reset_pixel == (0, 0, 255, 255)

init python:

    def texture_cull__pixel(cull_face):
        """Loads a straight-alpha texture right after a pass whose last model culled faces."""
        renpy.render_to_surface(Transform(Solid("#0f0", xysize=(64, 64)), gl_cull_face=cull_face), 64, 64)
        return tuple(renpy.display.draw.screenshot(texture_reset__make((255, 0, 0, 255))).get_at((32, 32)))

testsuite texture_cull_face:

    testcase upload_after_culled_pass:
        description "Culling left on by a pass removed the quad that premultiplies a texture, which then copied the framebuffer."
        parameter cull_face = ["cw", "ccw"]

        assert eval texture_cull__pixel(cull_face) == (255, 0, 0, 255)
