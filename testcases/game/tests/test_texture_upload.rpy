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

    def texture_upload__make():
        """Uses an odd-width subsurface so row pitch and the final short chunk are exercised."""
        import renpy.pygame as pygame
        surface = pygame.Surface((2050, 1025), pygame.SRCALPHA, 32)
        surface.fill((64, 0, 0, 128))
        surface.fill((0, 128, 0, 128), (0, 511, 2050, 3))
        surface.fill((0, 0, 255, 255), (0, 1023, 2050, 2))
        surface = surface.subsurface((1, 0, 2049, 1025))
        return renpy.display.draw.load_texture(surface, properties={"premultiplied": True, "mipmap": True})

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

    testcase demand_finishes_a_partial_upload:
        $ _upload_texture, _upload_loaded = texture_upload__begin()
        if eval config.texture_upload_chunk_size:
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
        if eval config.texture_upload_chunk_size:
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
