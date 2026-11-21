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

init python:

    renpy.register_shader("assimp_baked.depth", glsl=300, variables="""
        uniform vec4 u_baked_test_color;
    """, fragment_200="""
        fragment_color = u_baked_test_color;
    """)

    def assimp_integration__scene(blur=None, color=(0.25, 0.5, 0.75), glass=True, tone_mapping="reinhard", glass_blend=False):
        """Use only redistributable fixture geometry, with the same depth-in-alpha contract as a baked-room shader."""
        baked = Transform(GLTFModel("models/material-sided.gltf", zoom=100,
            shader=("assimp_baked.depth", "renpy.gltf.baked_srgb"), u_baked_test_color=(*color, 0.08)),
            xpos=200, ypos=150, zpos=-100)
        children = [baked]
        if glass:
            blend = {"u_material_gltf_alphamode": 2.0} if glass_blend else {}
            children.append(assimp_pbr__plane(u_material_transmission_factor=1.0, u_material_ior=1.0, **blend))
        scene = GLTFScene(assimp_alpha__camera(*children), light_color=(0, 0, 0),
            meters_per_unit=0.01, depth_output=blur is not None, tone_mapping=tone_mapping)
        return GLTFDepthOfField(scene, max_blur=blur, focus_distance=6.0) if blur is not None else scene

    renpy.register_shader("assimp_integration.distance", glsl=300, variables="""
        in vec2 a_tex_coord;
        out vec2 v__distance_uv;
    """, vertex_200="""
        v__distance_uv = a_tex_coord;
    """, fragment_300="""
        fragment_color = vec4(gltf_scene_distance(v__distance_uv) / 10.0, 0.0, 0.0, 1.0);
    """)

    def assimp_camera__scene(camera="MainOrthographic", **settings):
        source = GLTFModel("models/scene-cameras.gltf", scene=True, zoom=100, **settings)
        return GLTFCamera(source, camera=camera)

    def assimp_deform__scene(**settings):
        """Duplicate bone names and a translated mesh node expose name-based joints and accidental mesh transforms."""
        return Transform(GLTFCamera(GLTFModel("models/scene-deformation.gltf", scene=True, zoom=100, **settings)), xysize=(400, 300))

    renpy.register_shader("assimp_deform.frame", glsl=300, variables="""
        in vec3 a_normal;
        in vec3 a_tangent;
        out vec3 v_deform_frame;
        uniform float u_deform_frame;
    """, vertex_200="""
        v_deform_frame = u_deform_frame > 0.5 ? a_tangent : a_normal;
    """, fragment_200="""
        fragment_color = vec4(normalize(v_deform_frame) * 0.5 + 0.5, 1.0);
    """)

    # Unlit material shading makes the expected composite independent of lighting.
    renpy.register_shader("assimp_alpha.material", variables="""
        uniform sampler2D u_tex_diffuse;
        uniform vec4 u_color_diffuse;
        uniform float u_material_gltf_alphamode;
        uniform float u_material_gltf_alphacutoff;
        uniform vec4 u_assimp_alpha_tint;
        in vec2 a_tex_coord;
        out vec2 v_assimp_alpha_uv;
    """, vertex_201="""
        v_assimp_alpha_uv = a_tex_coord;
    """, fragment_201="""
        vec4 base = texture(u_tex_diffuse, v_assimp_alpha_uv) * u_color_diffuse * u_assimp_alpha_tint;
        if (u_material_gltf_alphamode < 0.5) {
            base.a = 1.0;
        } else if (u_material_gltf_alphamode < 1.5) {
            if (base.a < u_material_gltf_alphacutoff) {
                discard;
            }
            base.a = 1.0;
        }
        fragment_color = vec4(base.rgb * base.a, base.a);
    """)

    def assimp_alpha__plane(mode, tint=(1.0, 1.0, 1.0, 1.0), z=0, **properties):
        """Each fixture is a pane with white RGB, zero alpha on the left, and full alpha on the right."""
        model = GLTFModel("models/alpha-" + mode + ".gltf", shader="assimp_alpha.material",
            material_alpha=True, zoom=100, u_assimp_alpha_tint=tint)
        return Transform(model, xpos=200, ypos=150, zpos=z, gl_texture_scaling="nearest", **properties)

    def assimp_alpha__camera(*children, back=False):
        """The rear camera looks at the same panes from the other side, reversing their depth order."""
        return Transform(Fixed(*children, xysize=(400, 300)), xysize=(400, 300),
            perspective=(1, 500, 10000), gl_depth=True, zpos=-1000 if back else 0, yrotate=180 if back else 0)

    def assimp_alpha__pixels(displayable, positions=((150, 150), (250, 150))):
        """Sample away from texture boundaries; screenshots return straight-alpha RGBA."""
        surface = renpy.render_to_surface(displayable, 400, 300)
        width, height = surface.get_size()
        return [tuple(surface.get_at((int(x * width / 400), int(y * height / 300)))) for x, y in positions]

    def assimp_alpha__close(pixel, expected):
        """Allow two levels of rounding from GPU blending and screenshot alpha conversion."""
        return all(abs(a - b) <= 2 for a, b in zip(pixel, expected))

    def assimp_material__pixels(**kwargs):
        """Sample twelve independent materials in a grid without resizing screenshot alpha."""
        model = GLTFModel("models/material-grid.gltf", material=True, zoom=50, **kwargs)
        scene = Fixed(Transform(model, xpos=60, ypos=60), xysize=(500, 350))
        camera = Transform(scene, xysize=(500, 350), perspective=True, gl_depth=True)
        surface = renpy.render_to_surface(camera, 500, 350)
        w, h = surface.get_size()
        return [tuple(surface.get_at((int((60 + 110 * (i % 4)) * w / 500),
            int((60 + 110 * (i // 4)) * h / 350)))) for i in range(12)]

    renpy.register_shader("assimp_material.data", variables="""
        uniform sampler2D u_tex_emissive;
        uniform sampler2D u_tex_metalness;
        uniform sampler2D u_tex_normals;
    """, fragment_200="""
        fragment_color = vec4(texture(u_tex_emissive, vec2(0.5)).r,
            texture(u_tex_metalness, vec2(0.5)).r, texture(u_tex_normals, vec2(0.5)).r, 1.0);
    """)

    renpy.register_shader("assimp_layers.data", variables="""
        uniform sampler2D u_tex_specular;
        uniform sampler2D u_tex_specular_color;
        uniform sampler2D u_tex_clearcoat;
        uniform sampler2D u_tex_clearcoat_roughness;
        uniform sampler2D u_tex_clearcoat_normals;
        uniform sampler2D u_tex_sheen_color;
        uniform sampler2D u_tex_sheen_roughness;
        uniform float u_layers_test;
    """, fragment_200="""
        if (u_layers_test < 0.5) fragment_color = vec4(texture(u_tex_specular, vec2(0.5)).a,
            texture(u_tex_clearcoat, vec2(0.5)).r, texture(u_tex_clearcoat_roughness, vec2(0.5)).g, 1.0);
        else if (u_layers_test < 1.5) fragment_color = vec4(texture(u_tex_specular_color, vec2(0.5)).r,
            texture(u_tex_sheen_color, vec2(0.5)).r, texture(u_tex_sheen_roughness, vec2(0.5)).a, 1.0);
        else fragment_color = vec4(texture(u_tex_clearcoat_normals, vec2(0.5)).rgb, 1.0);
    """)

    def assimp_pbr__plane(color=(1, 1, 1, 1), z=0, **uniforms):
        """Use a real imported pane; texture alpha is overridden to isolate lighting from coverage."""
        uniforms.setdefault("u_material_metallicfactor", 0.0)
        return Transform(GLTFModel("models/alpha-opaque.gltf", shader="renpy.gltf.pbr", zoom=100,
            u_tex_base_color=im.Null("#fff"), u_color_base=color, **uniforms), xpos=200, ypos=150, zpos=z)

    def assimp_pbr__pixels(*children, back=False, **lighting):
        """Sample the optical center, where the analytic direct-light reference has V = N."""
        return assimp_alpha__pixels(GLTFScene(assimp_alpha__camera(*children, back=back), **lighting), ((200, 150),))[0]

    def assimp_lights__saved_scene(color):
        """Exercise real shadows and optical transmission together in a saved scene graph."""
        return GLTFScene(assimp_alpha__camera(assimp_pbr__plane(),
            assimp_pbr__plane(z=50, cast_shadows=False, u_material_transmission_factor=1.0, u_material_ior=1.0)),
            lights=(GLTFLight("directional", color=color, intensity=3.14159265359),), light_color=(0, 0, 0),
            import_lights=False, meters_per_unit=0.01, shadow_size=256, shadow_bias=0.001,
            shadow_cascades=2, shadow_distance=20, colored_shadows=True)

    def assimp_shadow__scene(kind="directional", change=None, cache=True):
        """Keep fixture geometry shared while varying one input to the standard shadow contract."""
        uniforms = {}
        if change == "alpha":
            uniforms["u_color_base"] = (1, 1, 1, 0)
        elif change == "texture":
            uniforms["u_tex_base_color"] = im.Null("#fff0")
        elif change == "uv":
            uniforms["u_material_base_color_uv0"] = (0, 0, 0)
            uniforms["u_material_base_color_uv1"] = (0, 0, 0)
        elif change == "cutoff":
            uniforms["u_material_gltf_alphacutoff"] = 0.0
        caster = Transform(GLTFModel("models/alpha-mask.gltf", shader="renpy.gltf.pbr", zoom=20,
            **uniforms), xpos=340 if change == "caster" else 240, ypos=150, zpos=50)
        receiver = assimp_pbr__plane((0.2, 0.5, 1, 1) if change == "rgb" else (1, 1, 1, 1))
        camera = assimp_alpha__camera(receiver, caster)
        if change == "camera":
            camera.xoffset = 10
        light = GLTFLight(kind, position=(400, 150, 200),
            direction=(1, 0, -1) if change == "light" else (-1, 0, -1),
            intensity=3.14159265359, outer_cone_angle=1.0)
        if change == "light" and kind == "point":
            light = GLTFLight(kind, position=(0, 150, 200), intensity=3.14159265359)
        return GLTFScene(camera, lights=(light,), light_color=(0, 0, 0),
            meters_per_unit=0.01, shadow_size=256, shadow_cache=cache)

    def assimp_shadow__sample(scene, st):
        """A readback synchronizes the image; timing queries themselves remain asynchronous."""
        import hashlib
        surface = renpy.render_to_surface(scene, 400, 300, st=st)
        return {"hash": hashlib.sha256(surface.get_buffer()).hexdigest(),
            "pixel": tuple(surface.get_at((surface.get_width() // 2, surface.get_height() // 2)))}

    def assimp_caustic__scene(zoom=20, **quality):
        """A lens outside the camera's center focuses an oblique light onto the visible receiver."""
        lens = Transform(GLTFModel("models/material-caustic-lens.gltf", shader="renpy.gltf.pbr", zoom=zoom), xpos=250, ypos=150, zpos=50)
        light = GLTFLight("directional", direction=(-1, 0, -1), intensity=3.14159265359)
        return GLTFScene(assimp_alpha__camera(assimp_pbr__plane(), lens), light_color=(0, 0, 0), lights=(light,),
            meters_per_unit=.01, caustics=True, shadow_size=512, **quality)

    def assimp_scene__model(**options):
        """Use the same cached source room for each independent instance and saved pose."""
        return GLTFModel("models/scene-controls.gltf", scene=True, zoom=100, **options)

    def assimp_scene__camera(**options):
        return assimp_alpha__camera(Transform(assimp_scene__model(**options), xpos=200, ypos=150))

    def assimp_scene__pixels(st=0.0, **options):
        """Sample the door, middle, and wall through the real renderer at a deterministic shown-time."""
        surface = renpy.render_to_surface(assimp_scene__camera(**options), 400, 300, st=st)
        width, height = surface.get_size()
        return [tuple(surface.get_at((int(x * width / 400), int(150 * height / 300)))) for x in (140, 200, 260)]

    def assimp_scene__screen_pixel(x):
        """Sample the last displayed render tree, preserving its real animation cache and shown-time."""
        surface = renpy.display.draw.screenshot(renpy.game.interface.surftree)
        return tuple(surface.get_at((int(x * surface.get_width() / config.screen_width),
            int(150 * surface.get_height() / config.screen_height))))

    def assimp_pbr__expected(rgb, alpha=1.0, exposure=1.0):
        """Independent display conversion for analytic radiance references, including HDR values above one."""
        result = []
        for channel in rgb:
            value = channel * exposure / (1.0 + channel * exposure)
            result.append(round(255 * (12.92 * value if value <= 0.0031308 else 1.055 * value ** (1 / 2.4) - 0.055)))
        return tuple(result) + (round(255 * alpha),)

    def assimp_pbr__linear(pixel):
        """Invert display conversion to compare a shadow tint against the same measured unshadowed radiance."""
        values = [channel / 255.0 for channel in pixel[:3]]
        values = [v / 12.92 if v <= .04045 else ((v + .055) / 1.055) ** 2.4 for v in values]
        return tuple(v / max(1 - v, 1e-6) for v in values)

    def assimp_pbr__tinted(pixel, tint, layers):
        """Apply independent per-surface transmission to a measured HDR reference."""
        return assimp_pbr__expected(tuple(a * b ** min(layers, 2) for a, b in zip(assimp_pbr__linear(pixel), tint)))

    def assimp_guard__matching(first, second):
        """Compare visible premultiplied color: straight RGB amplifies one-level rounding at nearly transparent DoF fringes."""
        a, b = [renpy.render_to_surface(i, 400, 300) for i in (first, second)]
        if a.get_size() != b.get_size():
            return False
        for y in range(a.get_height()):
            for x in range(a.get_width()):
                left, right = a.get_at((x, y)), b.get_at((x, y))
                if abs(left[3] - right[3]) > 2 or any(abs(c * left[3] - d * right[3]) > 2 * 255 for c, d in zip(left[:3], right[:3])):
                    return False
        return True

    renpy.register_shader("assimp_pbr.missing_uniform", variables="""
        uniform float u_assimp_pbr_missing;
    """, fragment_200="""
        fragment_color = vec4(u_assimp_pbr_missing);
    """)

    def assimp_pbr__failed_draw():
        """Trigger a real uniform error while the HDR framebuffer is attached, then let the testcase check recovery."""
        model = Transform(GLTFModel("models/alpha-opaque.gltf", material=True,
            shader="assimp_pbr.missing_uniform", zoom=100), xpos=200, ypos=150)
        try:
            assimp_alpha__pixels(GLTFScene(assimp_alpha__camera(model)))
        except renpy.gl2.gl2shader.ShaderError:
            return True
        return False

    renpy.register_shader("assimp_hdr.lod", variables="""
        uniform sampler2D tex0;
    """, fragment_200="""
        fragment_color = textureLod(tex0, vec2(0.1, 0.1), 2.0);
    """)

default assimp_alpha_saved_model = None

label assimp_integration_save:
    $ assimp_alpha_saved_model = assimp_integration__scene(blur=6)
    show expression assimp_alpha_saved_model as integrated_room
    "Integrated room ready."
    $ assimp_alpha_saved_model = assimp_integration__scene(blur=6, color=(0.75, 0.25, 0.5))
    show expression assimp_alpha_saved_model as integrated_room
    with Dissolve(0.05)
    "Integrated room changed."
    return

label assimp_camera_save:
    $ assimp_alpha_saved_model = assimp_camera__scene()
    show expression assimp_alpha_saved_model as imported_camera_room
    "Imported camera ready."
    $ assimp_alpha_saved_model = assimp_camera__scene(camera="SquarePerspective")
    show expression assimp_alpha_saved_model as imported_camera_room
    "Imported camera changed."
    return

label assimp_probe_save:
    $ _saved_probe = GLTFReflectionProbe(im.Null("#f00"), position=(200, 150, 0), box_min=(100, 50, -100), box_max=(300, 250, 100))
    $ assimp_alpha_saved_model = GLTFScene(assimp_alpha__camera(assimp_pbr__plane(u_material_metallicfactor=1.0, u_material_roughnessfactor=0.0, u_material_clearcoat_factor=0.5)), light_color=(0, 0, 0), reflection_probes=(_saved_probe,), environment_quality="medium")
    show expression assimp_alpha_saved_model as probe_room
    "Reflection probe ready."
    $ assimp_alpha_saved_model = None
    "Reflection probe changed."
    return

label assimp_deform_save:
    scene black
    show assimp_deform_blended as deform_room:
        pos (0, 0)
        anchor (0, 0)
    "Deformation starts."
    "Deformation ready."
    hide deform_room
    "Deformation changed."
    return

label assimp_deform_timed_save:
    scene black
    show assimp_deform_animated as deform_room:
        pos (0, 0)
        anchor (0, 0)
    "Deformation starts moving."
    "Deformation moving."
    return

label assimp_alpha_save:
    $ assimp_alpha_saved_model = GLTFModel("models/alpha-blend.gltf", shader="assimp_alpha.material", material_alpha=True, zoom=100, u_assimp_alpha_tint=(1, 0, 0, 1))
    "Glass ready to save."
    return

label assimp_material_save:
    $ assimp_alpha_saved_model = GLTFModel("models/alpha-blend.gltf", material=True, zoom=100, u_color_base=(1, 0, 0, 0.5), u_tex_base_color=im.Null("#fff"))
    "Glass ready to save."
    return

label assimp_pbr_save:
    $ assimp_alpha_saved_model = GLTFScene(assimp_alpha__camera(assimp_pbr__plane(u_material_metallicfactor=1.0, u_material_roughnessfactor=0.0)), light_color=(0, 0, 0), environment="models/material-environment.png", environment_rotation=180, exposure=0.5)
    "PBR ready to save."
    return

label assimp_hdr_save:
    $ assimp_alpha_saved_model = GLTFScene(assimp_alpha__camera(assimp_pbr__plane(u_material_metallicfactor=1.0, u_material_roughnessfactor=0.0)), light_color=(0, 0, 0), environment="models/material-environment.hdr", exposure=0.5)
    "HDR ready to save."
    return

label assimp_caustic_save:
    $ assimp_alpha_saved_model = assimp_caustic__scene()
    show expression assimp_alpha_saved_model as assimp_saved_model
    "The lens starts focusing."
    "The focused light is ready."
    return

label assimp_lights_save:
    $ assimp_alpha_saved_model = assimp_lights__saved_scene((1, 0, 0))
    "Red light ready."
    $ assimp_alpha_saved_model = assimp_lights__saved_scene((0, 0, 1))
    "Blue light ready."
    return

label assimp_scene_save:
    $ assimp_alpha_saved_model = assimp_scene__camera(animation="Slide", animation_time=1.0, material_overrides={"DoorPaint": {"u_color_base": (0, 1, 0, 1)}})
    "Door in the middle."
    $ assimp_alpha_saved_model = assimp_scene__camera(node_overrides={"Door": {"visible": False}})
    "Door hidden."
    return

screen assimp_scene_live(model):
    zorder 200
    add Solid("#000", xysize=(400, 300))
    add model pos (0, 0)

# Quarter speed covers x=200 from 2 to 6 seconds, leaving slow save/load timing room.
image assimp_scene_animated = assimp_scene__camera(animation="Slide", animation_speed=0.25)
image assimp_scene_animated_hdr = GLTFScene(assimp_scene__camera(animation="Slide", animation_speed=0.25), light_color=(0, 0, 0))
image assimp_scene_animated_alias = "assimp_scene_animated"
image assimp_scene_animated_box = Fixed("assimp_scene_animated")
image assimp_deform_animated = assimp_deform__scene(animation="Expand")
image assimp_deform_blended = assimp_deform__scene(animation="Bend", animation_time=2, animation_blend=("Expand", 1), animation_blend_duration=0.1)

label assimp_scene_timed_hdr_save:
    call assimp_scene_timed_save(True)
    return

label assimp_scene_timed_alias_save:
    call assimp_scene_timed_save(image_name="assimp_scene_animated_alias")
    return

label assimp_scene_timed_box_save:
    call assimp_scene_timed_save(image_name="assimp_scene_animated_box")
    return

label assimp_scene_timed_save(hdr=False, image_name=None):
    show expression (image_name or ("assimp_scene_animated_hdr" if hdr else "assimp_scene_animated")) as assimp_scene_anim:
        pos (0, 0)
        anchor (0, 0)
    "Door starts moving."
    "Door moving."
    return

testsuite assimp_alpha:

    testcase material_modes:
        parameter mode = ["opaque", "mask", "blend"]
        description "Opaque ignores alpha without losing RGB; cutouts use the imported cutoff; blending retains partial coverage."

        $ _alpha_pixels = assimp_alpha__pixels(assimp_alpha__camera(assimp_alpha__plane(mode)))
        assert eval assimp_alpha__close(_alpha_pixels[0], (255, 255, 255, 255) if mode == "opaque" else (0, 0, 0, 0))
        assert eval assimp_alpha__close(_alpha_pixels[1], (255, 255, 255, 128) if mode == "blend" else (255, 255, 255, 255))

    testcase scene_sorting:
        parameter order = [0, 1, 2]
        parameter back = [False, True]
        description "Separate model instances sort by camera depth regardless of scene order or camera side."

        $ _alpha_planes = dict(red=assimp_alpha__plane("blend", (1, 0, 0, 1), 40), blue=assimp_alpha__plane("blend", (0, 0, 1, 1), -40), green=assimp_alpha__plane("opaque", (0, 1, 0, 1), 200 if back else -200))
        $ _alpha_order = [("red", "blue", "green"), ("green", "blue", "red"), ("blue", "red", "green")][order]
        $ _alpha_camera = assimp_alpha__camera(*[_alpha_planes[name] for name in _alpha_order], back=back)
        $ _alpha_pixels = assimp_alpha__pixels(_alpha_camera)
        assert eval assimp_alpha__close(_alpha_pixels[0 if back else 1], (64, 63, 128, 255) if back else (128, 63, 64, 255))
        assert eval assimp_alpha__close(_alpha_pixels[1 if back else 0], (0, 255, 0, 255))

    testcase opaque_occlusion:
        description "Glass still tests against opaque depth, even when the opaque model was submitted later."

        $ _alpha_camera = assimp_alpha__camera(assimp_alpha__plane("blend", (1, 0, 0, 1)), assimp_alpha__plane("opaque", (0, 1, 0, 1), 100))
        $ _alpha_pixels = assimp_alpha__pixels(_alpha_camera)
        assert eval assimp_alpha__close(_alpha_pixels[1], (0, 255, 0, 255))

    testcase single_model_scene:
        description "Imported node transforms and materials in a single model share the same transparent pass."

        $ _alpha_pixels = assimp_alpha__pixels(assimp_alpha__camera(assimp_alpha__plane("scene")))
        assert eval assimp_alpha__close(_alpha_pixels[1], (128, 63, 64, 255))
        assert eval assimp_alpha__close(_alpha_pixels[0], (0, 255, 0, 255))

    testcase parent_opacity:
        description "Material alpha and a parent Transform's alpha multiply once each."

        $ _alpha_pixels = assimp_alpha__pixels(assimp_alpha__camera(assimp_alpha__plane("blend", alpha=0.5)))
        assert eval assimp_alpha__close(_alpha_pixels[1], (255, 255, 255, 64))

    testcase independent_depth_scopes:
        description "Two depth scenes finish their own transparent passes in display order."

        $ _alpha_red = assimp_alpha__camera(assimp_alpha__plane("blend", (1, 0, 0, 1), 40))
        $ _alpha_blue = assimp_alpha__camera(assimp_alpha__plane("blend", (0, 0, 1, 1), -40))
        $ _alpha_pixels = assimp_alpha__pixels(Fixed(_alpha_red, _alpha_blue, xysize=(400, 300)))
        assert eval assimp_alpha__close(_alpha_pixels[1], (85, 0, 170, 192))

    testcase clipped_transparency:
        description "Deferred draws retain their clip and do not paint outside a cropped camera."

        $ _alpha_camera = assimp_alpha__camera(assimp_alpha__plane("blend", (1, 0, 0, 1)))
        $ _alpha_crop = Transform(_alpha_camera, crop=(200, 100, 100, 100), xpos=200, ypos=100)
        $ _alpha_pixels = assimp_alpha__pixels(Fixed(_alpha_crop, xysize=(400, 300)), ((250, 150), (250, 75)))
        assert eval assimp_alpha__close(_alpha_pixels[0], (255, 0, 0, 128))
        assert eval _alpha_pixels[1] == (0, 0, 0, 0)

    testcase depth_writes_restored:
        description "A completed transparent pass leaves depth writes enabled for a later opaque scene."

        $ _alpha_pixels = assimp_alpha__pixels(assimp_alpha__camera(assimp_alpha__plane("blend")))
        $ _alpha_camera = assimp_alpha__camera(assimp_alpha__plane("opaque", (1, 0, 0, 1), 100), assimp_alpha__plane("opaque", (0, 0, 1, 1), -100))
        $ _alpha_pixels = assimp_alpha__pixels(_alpha_camera)
        assert eval assimp_alpha__close(_alpha_pixels[1], (255, 0, 0, 255))

    testcase save_load:
        parameter standard = [False, True]
        description "A model saved in the store restores its alpha option, textures, and rendered output."

        run Start("assimp_material_save" if standard else "assimp_alpha_save")
        advance until "Glass ready to save."
        run Function(renpy.save, "assimp-alpha-test")
        $ assimp_alpha_saved_model = None
        run Function(renpy.load, "assimp-alpha-test")
        pause until "Glass ready to save."
        assert eval assimp_alpha_saved_model.material_alpha
        assert eval assimp_alpha_saved_model.material is standard
        $ _alpha_model = Transform(assimp_alpha_saved_model, xpos=200, ypos=150, gl_texture_scaling="nearest")
        $ _alpha_pixels = assimp_alpha__pixels(assimp_alpha__camera(_alpha_model))
        assert eval assimp_alpha__close(_alpha_pixels[1], (255, 0, 0, 128))
        run Function(renpy.unlink_save, "assimp-alpha-test")
        run MainMenu(confirm=False)

testsuite assimp_material:

    testcase alpha_modes:
        parameter mode = ["opaque", "mask", "blend"]
        description "The built-in material combines the existing transparency pass with glTF's alpha modes."

        $ _material_model = GLTFModel("models/alpha-" + mode + ".gltf", material=True, zoom=100)
        $ _material_pixels = assimp_alpha__pixels(assimp_alpha__camera(Transform(_material_model, xpos=200, ypos=150)))
        assert eval assimp_alpha__close(_material_pixels[0], (255, 255, 255, 255) if mode == "opaque" else (0, 0, 0, 0))
        assert eval assimp_alpha__close(_material_pixels[1], (255, 255, 255, 128) if mode == "blend" else (255, 255, 255, 255))

    testcase texture_mapping:
        parameter panel = [0, 1, 2, 3, 4, 5, 6, 7]
        description "Base UVs, second UVs, transform overrides, wrapping, and rotated nonuniform scale follow glTF."

        $ _material_pixels = assimp_material__pixels()
        $ _material_expected = [(255, 0, 0, 255), (255, 255, 255, 255), (255, 255, 255, 255), (0, 0, 255, 255), (0, 255, 0, 255), (0, 255, 0, 255), (255, 0, 0, 255), (255, 255, 255, 255)]
        assert eval assimp_alpha__close(_material_pixels[panel], _material_expected[panel])

    testcase vertex_color:
        description "Linear vertex RGB and alpha multiply the material factor once each."

        $ _material_pixels = assimp_material__pixels()
        assert eval assimp_alpha__close(_material_pixels[8], (99, 188, 255, 64))

    testcase color_space:
        description "Texture factors and filtering operate in linear space; output rejoins Ren'Py's sRGB display."

        $ _material_pixels = assimp_material__pixels()
        assert eval assimp_alpha__close(_material_pixels[9], (92, 92, 92, 255))
        assert eval assimp_alpha__close(_material_pixels[10], (188, 188, 188, 255))
        assert eval assimp_alpha__close(_material_pixels[11], (188, 188, 188, 255))

    testcase material_override:
        description "A standard material's explicit model uniforms take precedence over imported factors."

        $ _material_model = GLTFModel("models/alpha-opaque.gltf", material=True, zoom=100, u_color_base=(0.5, 0.25, 1, 1))
        $ _material_camera = assimp_alpha__camera(Transform(_material_model, xpos=200, ypos=150))
        $ _material_pixels = assimp_alpha__pixels(_material_camera)
        assert eval assimp_alpha__close(_material_pixels[0], (188, 137, 255, 255))
        assert eval assimp_alpha__close(_material_pixels[1], (188, 137, 255, 255))

    testcase uv_override:
        description "UV binding uniforms can be overridden without being mistaken for texture displayables."

        $ _material_pixels = assimp_material__pixels(u_material_base_color_texcoord=1.0)
        assert eval assimp_alpha__close(_material_pixels[0], (255, 255, 255, 255))

    testcase texture_override:
        description "An overridden color texture retains the material's sampler and sRGB interpretation."

        $ _material_model = GLTFModel("models/alpha-opaque.gltf", material=True, zoom=100, u_color_base=(0.5, 0.5, 0.5, 1), u_tex_base_color=im.Null("#808080"))
        $ _material_pixels = assimp_alpha__pixels(assimp_alpha__camera(Transform(_material_model, xpos=200, ypos=150)))
        assert eval assimp_alpha__close(_material_pixels[1], (92, 92, 92, 255))

    testcase data_textures:
        description "One image used for emissive color is decoded as sRGB; metallic and normal maps retain linear channel values."

        $ _material_model = GLTFModel("models/material-data.gltf", material=True, shader="assimp_material.data", zoom=100)
        $ _material_pixels = assimp_alpha__pixels(assimp_alpha__camera(Transform(_material_model, xpos=200, ypos=150)))
        assert eval assimp_alpha__close(_material_pixels[1], (55, 128, 128, 255))

testsuite assimp_pbr:

    testcase direct_light:
        parameter back = [False, True]
        description "A front or back surface receives directional light in scene coordinates, with a linear GGX reference."

        $ _pbr_pixel = assimp_pbr__pixels(assimp_pbr__plane(), back=back, light_direction=(0, 0, -1 if back else 1), light_color=(3.14159265359, 3.14159265359, 3.14159265359))
        assert eval assimp_alpha__close(_pbr_pixel, assimp_pbr__expected((0.97, 0.97, 0.97)))

    testcase no_light:
        description "Reversing a directional light leaves a non-emissive surface dark."

        $ _pbr_pixel = assimp_pbr__pixels(assimp_pbr__plane(), light_direction=(0, 0, -1))
        assert eval assimp_alpha__close(_pbr_pixel, (0, 0, 0, 255))

    testcase roughness:
        parameter roughness = [0.5, 1.0]
        description "Metallic highlights respond to squared perceptual roughness before HDR tone mapping."

        $ _pbr_pixel = assimp_pbr__pixels(assimp_pbr__plane(u_material_metallicfactor=1.0, u_material_roughnessfactor=roughness), light_direction=(0, 0, 1), light_color=(3.14159265359, 3.14159265359, 3.14159265359))
        $ _pbr_radiance = 1.0 / (4.0 * roughness ** 4)
        assert eval assimp_alpha__close(_pbr_pixel, assimp_pbr__expected((_pbr_radiance,) * 3))

    testcase emissive_hdr:
        parameter exposure = [0.25, 1.0, 2.0]
        description "Emission is linear and retains values above one in the HDR target, with exposure applied once."

        $ _pbr_pixel = assimp_pbr__pixels(assimp_pbr__plane(u_material_emissivefactor=(4, 1, 0.25)), light_color=(0, 0, 0), exposure=exposure)
        assert eval assimp_alpha__close(_pbr_pixel, assimp_pbr__expected((4, 1, 0.25), exposure=exposure))

    testcase packed_material_map:
        description "Metallic and roughness use the blue and green channels without sRGB decoding."

        $ _pbr_pixel = assimp_pbr__pixels(assimp_pbr__plane(u_material_metallicfactor=1.0, u_tex_metalness=im.Null("#80ff00")), light_direction=(0, 0, 1), light_color=(3.14159265359,) * 3)
        assert eval assimp_alpha__close(_pbr_pixel, assimp_pbr__expected((0.97,) * 3))

    testcase normal_map:
        parameter scale = [0.0, 1.0]
        description "A tangent-space normal texture changes the lighting; normal scale zero restores the geometric normal."

        $ _pbr_pixel = assimp_pbr__pixels(assimp_pbr__plane(u_tex_normals=im.Null("#ff8080"), u_material_normal_scale=scale), light_direction=(0, 0, 1), light_color=(3.14159265359,) * 3)
        $ _pbr_radiance = 0.97 if scale == 0.0 else 0.96 / (255 ** 2 + 2) ** 0.5 + 0.01
        assert eval assimp_alpha__close(_pbr_pixel, assimp_pbr__expected((_pbr_radiance,) * 3))

    testcase emissive_texture:
        description "Emissive color is decoded from sRGB and multiplied by its linear factor."

        $ _pbr_pixel = assimp_pbr__pixels(assimp_pbr__plane(u_tex_emissive=im.Null("#808080"), u_material_emissivefactor=(4, 4, 4)), light_color=(0, 0, 0))
        assert eval assimp_alpha__close(_pbr_pixel, assimp_pbr__expected((4 * ((128 / 255 + 0.055) / 1.055) ** 2.4,) * 3))

    testcase linear_transparency:
        parameter reverse = [False, True]
        description "Transparent materials sort before blending in linear HDR; tone mapping follows composition."

        $ _pbr_red = assimp_pbr__plane((1, 1, 1, 0.5), z=40, u_material_gltf_alphamode=2.0, u_material_emissivefactor=(1, 0, 0))
        $ _pbr_blue = assimp_pbr__plane(z=-40, u_material_emissivefactor=(0, 0, 1))
        $ _pbr_pixel = assimp_pbr__pixels(*([_pbr_red, _pbr_blue] if reverse else [_pbr_blue, _pbr_red]), light_color=(0, 0, 0))
        assert eval assimp_alpha__close(_pbr_pixel, assimp_pbr__expected((0.5, 0, 0.5)))

    testcase partial_alpha:
        description "Tone mapping unpremultiplies linear light before encoding, preserving partial alpha."

        $ _pbr_pixel = assimp_pbr__pixels(assimp_pbr__plane((1, 1, 1, 0.5), u_material_gltf_alphamode=2.0, u_material_emissivefactor=(4, 1, 0.25)), light_color=(0, 0, 0))
        assert eval assimp_alpha__close(_pbr_pixel, assimp_pbr__expected((4, 1, 0.25), alpha=0.5))

    testcase white_environment:
        description "A uniform environment matches the integrated single-scattering GGX reference at maximum roughness."

        $ _pbr_pixel = assimp_pbr__pixels(assimp_pbr__plane(u_material_metallicfactor=1.0), light_color=(0, 0, 0), environment=im.Null("#fff"))
        assert eval assimp_alpha__close(_pbr_pixel, assimp_pbr__expected((1 - 0.69314718056,) * 3))

    testcase environment_rotation:
        parameter rotation = [0, 180]
        parameter back = [False, True]
        description "Reflections follow both the viewing camera and the probe's scene-space rotation."

        $ _pbr_pixel = assimp_pbr__pixels(assimp_pbr__plane(u_material_metallicfactor=1.0, u_material_roughnessfactor=0.0), back=back, light_color=(0, 0, 0), environment="models/material-environment.png", environment_rotation=rotation)
        assert eval assimp_alpha__close(_pbr_pixel, assimp_pbr__expected((1, 0, 0) if (rotation == 0) != back else (0, 0, 1)))

    testcase environment_roughness:
        description "A rough reflection mixes the probe's narrow red stripe with surrounding blue radiance."

        $ _pbr_pixel = assimp_pbr__pixels(assimp_pbr__plane(u_material_metallicfactor=1.0), light_color=(0, 0, 0), environment="models/material-environment.png")
        assert eval _pbr_pixel[0] > 10 and _pbr_pixel[2] > _pbr_pixel[0] and _pbr_pixel[1] < 3

    testcase occlusion:
        parameter strength = [0.0, 1.0]
        description "Occlusion modulates indirect light, and its strength zero restores the unoccluded result."

        $ _pbr_pixel = assimp_pbr__pixels(assimp_pbr__plane(u_material_metallicfactor=1.0, u_tex_lightmap=im.Null("#000"), u_material_occlusion_strength=strength), light_color=(0, 0, 0), environment=im.Null("#fff"))
        assert eval assimp_alpha__close(_pbr_pixel, assimp_pbr__expected(((1 - 0.69314718056) * (1 - strength),) * 3))

    testcase occlusion_preserves_direct_light:
        description "A dark occlusion map does not suppress direct illumination."

        $ _pbr_pixel = assimp_pbr__pixels(assimp_pbr__plane(u_tex_lightmap=im.Null("#000")), light_direction=(0, 0, 1), light_color=(3.14159265359,) * 3, environment=im.Null("#fff"))
        assert eval assimp_alpha__close(_pbr_pixel, assimp_pbr__expected((0.97,) * 3))

    testcase normal_texture_coordinates:
        parameter mapping = ["uv1", "uv0", "offset"]
        description "Normal maps use their own UV selection and transformed differential basis."

        $ _pbr_options = {} if mapping == "uv1" else {"u_material_normals_texcoord": 0.0} if mapping == "uv0" else {"u_material_normals_uv0": (1, 0, -0.5)}
        $ _pbr_model = Transform(GLTFModel("models/material-normals.gltf", shader="renpy.gltf.pbr", zoom=100, **_pbr_options), xpos=200, ypos=150)
        $ _pbr_pixel = assimp_pbr__pixels(_pbr_model, light_direction=(0, 0, 1), light_color=(3.14159265359,) * 3)
        $ _pbr_radiance = 0.97 if mapping == "uv1" else 0.96 / (255 ** 2 + 2) ** 0.5 + 0.01
        assert eval assimp_alpha__close(_pbr_pixel, assimp_pbr__expected((_pbr_radiance,) * 3))

    testcase unlit_material:
        description "The glTF unlit material bypasses lights, normal maps, and emission inside a linear PBR scene."

        $ _pbr_pixel = assimp_pbr__pixels(assimp_pbr__plane((0.5, 0.25, 1, 1), u_material_unlit=1.0, u_material_emissivefactor=(10, 10, 10)), light_color=(0, 0, 0))
        assert eval assimp_alpha__close(_pbr_pixel, assimp_pbr__expected((0.5, 0.25, 1)))

    testcase unlit_preview_in_scene:
        description "The existing unlit preview outputs linear light when used inside a PBR scene."

        $ _pbr_model = Transform(GLTFModel("models/alpha-opaque.gltf", material=True, zoom=100, u_color_base=(0.5, 0.25, 1, 1)), xpos=200, ypos=150)
        $ _pbr_pixel = assimp_pbr__pixels(_pbr_model)
        assert eval assimp_alpha__close(_pbr_pixel, assimp_pbr__expected((0.5, 0.25, 1)))

    testcase transformed_normals:
        parameter scale = [2.0, -2.0]
        description "Inverse-transpose normals keep their lighting under nonuniform and mirrored model transforms."

        $ _pbr_model = Transform(GLTFModel("models/alpha-opaque.gltf", shader="renpy.gltf.pbr", zoom=100, u_color_base=(1, 1, 1, 1), u_material_metallicfactor=0.0), xpos=200, ypos=150, matrixtransform=Matrix.scale(scale, 1, 1) * Matrix.rotate(0, 60, 0))
        $ _pbr_pixel = assimp_pbr__pixels(_pbr_model, light_direction=(0, 0, 1), light_color=(3.14159265359,) * 3)
        assert eval assimp_alpha__close(_pbr_pixel, assimp_pbr__expected((0.96 * (4 / 7) ** 0.5 + 0.01,) * 3))

    testcase single_sided_winding:
        parameter mirrored = [False, True]
        parameter back = [False, True]
        description "Mirroring a single-sided model preserves its front surface and continues to cull its back."

        $ _pbr_model = Transform(GLTFModel("models/material-sided.gltf", shader="renpy.gltf.pbr", zoom=100), xpos=200, ypos=150, xzoom=-1 if mirrored else 1)
        $ _pbr_pixel = assimp_pbr__pixels(_pbr_model, back=back, light_direction=(0, 0, -1 if back else 1), light_color=(3.14159265359,) * 3)
        assert eval assimp_alpha__close(_pbr_pixel, (0, 0, 0, 0) if back else assimp_pbr__expected((0.97,) * 3))

    testcase save_load:
        description "Saving and loading a PBR scene restores its environment graph, exposure, and reflected output."

        run Start("assimp_pbr_save")
        advance until "PBR ready to save."
        run Function(renpy.save, "assimp-pbr-test")
        $ assimp_alpha_saved_model = None
        run Function(renpy.load, "assimp-pbr-test")
        pause until "PBR ready to save."
        assert eval assimp_alpha_saved_model.exposure == 0.5
        $ _pbr_pixel = assimp_alpha__pixels(assimp_alpha_saved_model, ((200, 150),))[0]
        assert eval assimp_alpha__close(_pbr_pixel, assimp_pbr__expected((0, 0, 1), exposure=0.5))
        run Function(renpy.unlink_save, "assimp-pbr-test")
        run MainMenu(confirm=False)

    testcase failed_draw_restores_framebuffer:
        description "An exception during an HDR draw restores the normal target before a later legacy draw."

        $ _pbr_failed = assimp_pbr__failed_draw()
        assert eval _pbr_failed
        $ _pbr_model = Transform(GLTFModel("models/alpha-opaque.gltf", material=True, zoom=100), xpos=200, ypos=150)
        $ _pbr_pixel = assimp_alpha__pixels(assimp_alpha__camera(_pbr_model), ((200, 150),))[0]
        assert eval assimp_alpha__close(_pbr_pixel, (255, 255, 255, 255))

testsuite assimp_hdr:

    testcase radiance_reflection:
        parameter rotation = [0, 180]
        parameter exposure = [0.25, 1.0]
        description "An HDR environment preserves radiance above one through reflection, rotation, and exposure."

        $ _hdr_pixel = assimp_pbr__pixels(assimp_pbr__plane(u_material_metallicfactor=1.0, u_material_roughnessfactor=0.0), light_color=(0, 0, 0), environment="models/material-environment.hdr", environment_rotation=rotation, exposure=exposure)
        assert eval assimp_alpha__close(_hdr_pixel, assimp_pbr__expected((4, 0, 0) if rotation == 0 else (0, 0, 2), exposure=exposure))

    testcase linear_mipmaps:
        description "HDR source mipmaps average linear half-float radiance without clipping highlights or sRGB decoding."

        $ _hdr_model = Model(size=(400, 300)).texture("models/material-checker.hdr", premultiply=False).shader("assimp_hdr.lod")
        $ _hdr_pixel = assimp_alpha__pixels(GLTFScene(_hdr_model), ((200, 150),))[0]
        assert eval assimp_alpha__close(_hdr_pixel, assimp_pbr__expected((4, 1, 0.25)))

    testcase rough_reflection:
        description "Rough reflections average high-frequency HDR radiance using the source's linear mip chain."

        $ _hdr_reference = assimp_pbr__pixels(assimp_pbr__plane(u_material_metallicfactor=1.0), light_color=(0, 0, 0), environment="models/material-uniform.hdr")
        $ _hdr_pixel = assimp_pbr__pixels(assimp_pbr__plane(u_material_metallicfactor=1.0), light_color=(0, 0, 0), environment="models/material-checker.hdr")
        assert eval assimp_alpha__close(_hdr_pixel, _hdr_reference)

    testcase authored_emissive_strength:
        parameter strength = [0, 1, 4]
        description "The imported glTF multiplier increases emission, and explicit model overrides take precedence."

        $ _hdr_options = {} if strength == 4 else {"u_material_emissive_strength": float(strength)}
        $ _hdr_model = Transform(GLTFModel("models/material-emission.gltf", shader="renpy.gltf.pbr", zoom=100, **_hdr_options), xpos=200, ypos=150)
        $ _hdr_pixel = assimp_pbr__pixels(_hdr_model, light_color=(0, 0, 0))
        assert eval assimp_alpha__close(_hdr_pixel, assimp_pbr__expected((strength, strength * 0.25, strength * 0.0625)))

    testcase save_load:
        description "Saving and loading a scene retains a linear HDR environment and its exposure."

        run Start("assimp_hdr_save")
        advance until "HDR ready to save."
        run Function(renpy.save, "assimp-hdr-test")
        $ assimp_alpha_saved_model = None
        run Function(renpy.load, "assimp-hdr-test")
        pause until "HDR ready to save."
        $ _hdr_pixel = assimp_alpha__pixels(assimp_alpha_saved_model, ((200, 150),))[0]
        assert eval assimp_alpha__close(_hdr_pixel, assimp_pbr__expected((4, 0, 0), exposure=0.5))
        run Function(renpy.unlink_save, "assimp-hdr-test")
        run MainMenu(confirm=False)

testsuite assimp_lights:

    testcase custom_material:
        description "A legacy custom material can join HDR composition without supplying the standard shadow texture contract."

        $ _light_pixel = assimp_pbr__pixels(assimp_alpha__plane("opaque", (1, 0, 0, 1)))
        assert eval assimp_alpha__close(_light_pixel, assimp_pbr__expected((1, 0, 0)))

    testcase imported_lights:
        parameter kind = ["directional", "point", "spot"]
        description "Authored glTF lights use their node placement and physical distance after model zoom and scene placement."

        $ _light_model = Transform(GLTFModel("models/light-" + kind + ".gltf", shader="renpy.gltf.pbr", zoom=100), xpos=200, ypos=150)
        $ _light_pixel = assimp_pbr__pixels(_light_model, light_color=(0, 0, 0), meters_per_unit=0.01)
        assert eval assimp_alpha__close(_light_pixel, assimp_pbr__expected((0.97,) * 3))

    testcase point_attenuation:
        parameter distance = [100, 200]
        description "Point light irradiance falls by four when the distance doubles."

        $ _light = GLTFLight("point", position=(200, 150, distance), intensity=3.14159265359)
        $ _light_pixel = assimp_pbr__pixels(assimp_pbr__plane(), light_color=(0, 0, 0), lights=(_light,), meters_per_unit=0.01)
        assert eval assimp_alpha__close(_light_pixel, assimp_pbr__expected((0.97 / (distance / 100) ** 2,) * 3))

    testcase spot_and_range:
        parameter mode = ["inside", "outside", "range"]
        description "Spot cones and finite light ranges suppress illumination outside their authored limits."

        $ _light = GLTFLight("spot", position=(200, 150, 100), direction=(1, 0, 0) if mode == "outside" else (0, 0, -1), intensity=3.14159265359, range=1.0 if mode == "range" else None)
        $ _light_pixel = assimp_pbr__pixels(assimp_pbr__plane(), light_color=(0, 0, 0), lights=(_light,), meters_per_unit=0.01)
        assert eval assimp_alpha__close(_light_pixel, assimp_pbr__expected((0.97,) * 3) if mode == "inside" else (0, 0, 0, 255))

    testcase multiple_lights:
        description "Independent colored lights add in linear HDR before the final tone mapping."

        $ _lights = (GLTFLight("directional", color=(1, 0, 0), intensity=3.14159265359), GLTFLight("point", position=(200, 150, 100), color=(0, 0, 1), intensity=3.14159265359))
        $ _light_pixel = assimp_pbr__pixels(assimp_pbr__plane(), light_color=(0, 0, 0), lights=_lights, meters_per_unit=0.01)
        assert eval assimp_alpha__close(_light_pixel, assimp_pbr__expected((0.97, 0, 0.97)))

    testcase imported_override:
        description "A named imported-light override can disable a light without changing the shared model import."

        $ _light_model = Transform(GLTFModel("models/light-point.gltf", shader="renpy.gltf.pbr", zoom=100, light_overrides={"Lamp": {"intensity": 0}}), xpos=200, ypos=150)
        $ _light_pixel = assimp_pbr__pixels(_light_model, light_color=(0, 0, 0), meters_per_unit=0.01)
        assert eval assimp_alpha__close(_light_pixel, (0, 0, 0, 255))

    testcase directional_shadow:
        parameter kind = ["directional", "point", "spot"]
        parameter enabled = [False, True]
        description "An off-center occluder casts a shadow on the receiver without covering the camera's sample."

        $ _receiver = assimp_pbr__plane()
        $ _occluder = Transform(GLTFModel("models/material-sided.gltf", shader="renpy.gltf.pbr", zoom=20), xpos=250, ypos=150, zpos=50)
        $ _light = GLTFLight(kind, position=(400, 150, 200), direction=(-1, 0, -1), intensity=3.14159265359, outer_cone_angle=1.0)
        $ _light_pixel = assimp_pbr__pixels(_receiver, _occluder, light_color=(0, 0, 0), lights=(_light,), shadows=enabled, meters_per_unit=0.01)
        assert eval (_light_pixel[0] < 4 if enabled else _light_pixel[0] > 30)

    testcase hemispherical_spot_shadow:
        description "A 180-degree spotlight uses radial shadow faces rather than a degenerate perspective projection."

        $ _occluder = Transform(GLTFModel("models/material-sided.gltf", shader="renpy.gltf.pbr", zoom=20), xpos=250, ypos=150, zpos=50)
        $ _light_pixel = assimp_pbr__pixels(assimp_pbr__plane(), _occluder, light_color=(0, 0, 0), lights=(GLTFLight("spot", position=(400, 150, 200), direction=(-1, 0, -1), intensity=3.14159265359, outer_cone_angle=3.141592653589793 / 2),), meters_per_unit=0.01)
        assert eval _light_pixel[0] < 4

    testcase cutout_shadow:
        parameter position = [240, 260]
        description "A cutout caster transmits light through the discarded side of its alpha texture."

        $ _receiver = assimp_pbr__plane()
        $ _occluder = Transform(GLTFModel("models/alpha-mask.gltf", shader="renpy.gltf.pbr", zoom=20), xpos=position, ypos=150, zpos=50)
        $ _light = GLTFLight("directional", direction=(-1, 0, -1), intensity=3.14159265359)
        $ _light_pixel = assimp_pbr__pixels(_receiver, _occluder, light_color=(0, 0, 0), lights=(_light,))
        assert eval (_light_pixel[0] < 4 if position == 240 else _light_pixel[0] > 30)

    testcase point_shadow_faces:
        parameter face = [0, 1, 2, 3, 4, 5]
        description "All six point-light atlas faces agree with scene-space directions and radial depth."

        $ _position = [(400, 150, 100), (0, 150, 100), (200, 350, 100), (200, -50, 100), (250, 150, 200), (250, 150, -200)][face]
        $ _caster_position = tuple(a + (b - a) * 0.25 for a, b in zip((200, 150, 0), _position))
        $ _occluder = Transform(GLTFModel("models/material-sided.gltf", shader="renpy.gltf.pbr", zoom=5), xpos=absolute(_caster_position[0]), ypos=absolute(_caster_position[1]), zpos=_caster_position[2])
        $ _light_pixel = assimp_pbr__pixels(assimp_pbr__plane(), _occluder, back=(face == 5), light_color=(0, 0, 0), lights=(GLTFLight("point", position=_position, intensity=3.14159265359),), meters_per_unit=0.01)
        assert eval _light_pixel[0] < 4

    testcase shadow_opt_out:
        parameter receiver = [False, True]
        description "Per-model shadow casting and receiving options disable shadowing independently."

        $ _receiver = assimp_pbr__plane(receive_shadows=not receiver)
        $ _occluder = Transform(GLTFModel("models/material-sided.gltf", shader="renpy.gltf.pbr", zoom=20, cast_shadows=receiver), xpos=250, ypos=150, zpos=50)
        $ _light_pixel = assimp_pbr__pixels(_receiver, _occluder, light_color=(0, 0, 0), lights=(GLTFLight("directional", direction=(-1, 0, -1), intensity=3.14159265359),))
        assert eval _light_pixel[0] > 30

    testcase transmission_shadow:
        parameter position = [240, 260]
        parameter enabled = [False, True]
        description "Opaque areas of a transmission map cast shadows; disabling transmission restores opaque shadows."

        $ _occluder = Transform(GLTFModel("models/material-transmission-map.gltf", shader="renpy.gltf.pbr", zoom=20), xpos=position, ypos=150, zpos=50)
        $ _light_pixel = assimp_pbr__pixels(assimp_pbr__plane(), _occluder, light_color=(0, 0, 0), lights=(GLTFLight("directional", direction=(-1, 0, -1), intensity=3.14159265359),), transmission=enabled)
        assert eval (_light_pixel[0] > 30 if enabled and position == 240 else _light_pixel[0] < 4)

    testcase save_load:
        description "Lights, shadow settings, and optical glass survive a real save/load and render again."

        run Start("assimp_lights_save")
        advance until "Red light ready."
        run Function(renpy.save, "assimp-lights-test")
        $ assimp_alpha_saved_model = None
        run Function(renpy.load, "assimp-lights-test")
        pause until "Red light ready."
        assert eval assimp_alpha_saved_model.shadows and assimp_alpha_saved_model.transmission
        assert eval not assimp_alpha_saved_model.import_lights and assimp_alpha_saved_model.shadow_size == 256
        assert eval assimp_alpha_saved_model.shadow_bias == 0.001 and assimp_alpha_saved_model.meters_per_unit == 0.01
        assert eval assimp_alpha_saved_model.shadow_cascades == 2 and assimp_alpha_saved_model.colored_shadows
        $ _light_pixel = assimp_alpha__pixels(assimp_alpha_saved_model, ((200, 150),))[0]
        assert eval assimp_alpha__close(_light_pixel, assimp_pbr__expected((0.97, 0, 0)))
        run Function(renpy.unlink_save, "assimp-lights-test")
        run MainMenu(confirm=False)

    testcase rollback:
        description "Replacing a light scene across a checkpoint restores both lighting and glass after rollback."

        run Start("assimp_lights_save")
        advance until "Red light ready."
        advance until "Blue light ready."
        $ _light_pixel = assimp_alpha__pixels(assimp_alpha_saved_model, ((200, 150),))[0]
        assert eval assimp_alpha__close(_light_pixel, assimp_pbr__expected((0, 0, 0.97)))
        run Rollback()
        pause until "Red light ready."
        $ _light_pixel = assimp_alpha__pixels(assimp_alpha_saved_model, ((200, 150),))[0]
        assert eval assimp_alpha__close(_light_pixel, assimp_pbr__expected((0.97, 0, 0)))
        run MainMenu(confirm=False)

testsuite assimp_transmission:

    testcase thin_transmission:
        parameter amount = [0.0, 0.5, 1.0]
        description "Optical transmission retains opaque coverage and transmits the HDR scene beneath the glass."

        $ _glass_back = assimp_pbr__plane(z=-100, u_material_emissivefactor=(4, 1, 0.25))
        $ _glass = assimp_pbr__plane(z=0, u_material_transmission_factor=amount, u_material_ior=1.0)
        $ _glass_pixel = assimp_pbr__pixels(_glass, _glass_back, light_color=(0, 0, 0))
        assert eval assimp_alpha__close(_glass_pixel, assimp_pbr__expected((4 * amount, amount, 0.25 * amount)))

    testcase layered_glass:
        parameter reverse = [False, True]
        description "Each glass layer samples the scene containing farther glass, regardless of submission order."

        $ _glass_back = assimp_pbr__plane(z=-100, u_material_emissivefactor=(1, 1, 1))
        $ _glass_red = assimp_pbr__plane((1, 0.5, 0.5, 1), z=40, u_material_transmission_factor=1.0, u_material_ior=1.0)
        $ _glass_blue = assimp_pbr__plane((0.5, 0.5, 1, 1), z=0, u_material_transmission_factor=1.0, u_material_ior=1.0)
        $ _glass_layers = [_glass_red, _glass_back, _glass_blue] if reverse else [_glass_blue, _glass_red, _glass_back]
        $ _glass_pixel = assimp_pbr__pixels(*_glass_layers, light_color=(0, 0, 0))
        assert eval assimp_alpha__close(_glass_pixel, assimp_pbr__expected((0.5, 0.25, 0.5)))

    testcase copy_batching:
        parameter overlap = [False, True]
        parameter roughness = [0.0, 1.0]
        description "Disjoint smooth thin panes share a copy; overlapping and rough panes preserve layer samples."

        $ _glass_first = Transform(assimp_pbr__plane(u_material_transmission_factor=1.0, u_material_roughnessfactor=roughness).child, xpos=150, ypos=150, xzoom=0.25)
        $ _glass_second = Transform(assimp_pbr__plane((0.5, 1, 0.5, 1), u_material_transmission_factor=1.0, u_material_roughnessfactor=roughness).child, xpos=150 if overlap else 250, ypos=150, xzoom=0.25, zpos=50)
        $ _glass_background = assimp_pbr__plane(z=-100, u_material_emissivefactor=(1, 0.5, 0.25))
        $ _glass_camera = assimp_alpha__camera(_glass_background, _glass_first, _glass_second)
        $ renpy.display.draw.set_gltf_profiling()
        $ _glass_batched = assimp_shadow__sample(GLTFScene(_glass_camera, light_color=(0, 0, 0)), 0)
        $ _glass_profile = renpy.display.draw.get_gltf_profile()
        $ renpy.display.draw.set_gltf_profiling(False)
        $ _glass_reference = assimp_shadow__sample(GLTFScene(_glass_camera, light_color=(0, 0, 0), refraction_batch=False), 0.1)
        assert eval _glass_batched["hash"] == _glass_reference["hash"]
        assert eval _glass_profile["passes"]["refraction_copy"]["copies"] == (1 if not overlap and not roughness else 2)

    testcase volume_absorption:
        description "Beer-Lambert absorption uses transformed thickness and physical scene distance."

        $ _glass_back = assimp_pbr__plane(z=-100, u_material_emissivefactor=(1, 1, 1))
        $ _glass = assimp_pbr__plane(u_material_transmission_factor=1.0, u_material_ior=1.0, u_material_thickness_factor=0.5, u_material_attenuation_distance=0.5, u_material_attenuation_color=(0.25, 0.5, 1))
        $ _glass_pixel = assimp_pbr__pixels(_glass, _glass_back, light_color=(0, 0, 0), meters_per_unit=0.01)
        assert eval assimp_alpha__close(_glass_pixel, assimp_pbr__expected((0.25, 0.5, 1)))

    testcase transmission_texture:
        description "The red transmission channel controls optical transparency without changing opaque coverage."

        $ _glass = Transform(GLTFModel("models/material-transmission-map.gltf", shader="renpy.gltf.pbr", zoom=100), xpos=200, ypos=150)
        $ _glass_back = assimp_pbr__plane(z=-100, u_material_emissivefactor=(1, 1, 1))
        $ _glass_pixels = assimp_alpha__pixels(GLTFScene(assimp_alpha__camera(_glass, _glass_back), light_color=(0, 0, 0)))
        assert eval assimp_alpha__close(_glass_pixels[0], (0, 0, 0, 255))
        assert eval assimp_alpha__close(_glass_pixels[1], assimp_pbr__expected((1, 1, 1)))

    testcase thickness_texture:
        description "A green-channel thickness texture works without a transmission texture and stays linear."

        $ _glass = Transform(GLTFModel("models/material-thickness-map.gltf", shader="renpy.gltf.pbr", zoom=100), xpos=200, ypos=150)
        $ _glass_back = assimp_pbr__plane(z=-100, u_material_emissivefactor=(1, 1, 1))
        $ _glass_pixels = assimp_alpha__pixels(GLTFScene(assimp_alpha__camera(_glass, _glass_back), light_color=(0, 0, 0), meters_per_unit=0.01))
        assert eval assimp_alpha__close(_glass_pixels[0], assimp_pbr__expected((0.25 ** (128/255), 0.5 ** (128/255), 1)))
        assert eval assimp_alpha__close(_glass_pixels[1], assimp_pbr__expected((0.25, 0.5, 1)))

    testcase refraction:
        parameter ior = [1.0, 1.5]
        parameter thickness = [0.0, 1.0]
        description "Only a volume with IOR above one bends the view through a tilted surface."

        $ _glass = Transform(GLTFModel("models/material-sided.gltf", shader="renpy.gltf.pbr", zoom=100, u_material_transmission_factor=1.0, u_material_ior=ior, u_material_thickness_factor=thickness, u_material_roughnessfactor=0.0), xpos=200, ypos=150, yrotate=45)
        $ _glass_back = Transform(GLTFModel("models/material-refraction-background.gltf", shader="renpy.gltf.pbr", zoom=200), xpos=200, ypos=150, zpos=-200)
        $ _glass_pixel = assimp_pbr__pixels(_glass, _glass_back, light_color=(0, 0, 0))
        assert eval (_glass_pixel[0] > 180 and _glass_pixel[2] < 3 if ior > 1.0 and thickness > 0 else _glass_pixel[2] > 180 and _glass_pixel[0] < 3)

    testcase frosted_glass:
        parameter roughness = [0.0, 1.0]
        description "Rough transmission samples the linear HDR mip chain to blur the background."

        $ _glass = assimp_pbr__plane(u_material_transmission_factor=1.0, u_material_roughnessfactor=roughness)
        $ _glass_back = Transform(GLTFModel("models/material-refraction-checker.gltf", shader="renpy.gltf.pbr", zoom=200), xpos=200, ypos=150, zpos=-200)
        $ _glass_pixel = assimp_pbr__pixels(_glass, _glass_back, light_color=(0, 0, 0))
        assert eval (min(_glass_pixel[0], _glass_pixel[2]) > 30 and abs(_glass_pixel[0] - _glass_pixel[2]) < 3 if roughness else max(_glass_pixel[0], _glass_pixel[2]) > 180 and min(_glass_pixel[0], _glass_pixel[2]) < 3)

    testcase internal_reflection:
        parameter ior = [1.0, 1.5]
        description "A camera inside a closed volume sees the exit surface and total internal reflection at a critical angle."

        $ _glass = Transform(GLTFModel("models/material-glass-cube.gltf", shader="renpy.gltf.pbr", zoom=100, u_material_ior=ior), xpos=200, ypos=150, zpos=500, yrotate=45)
        $ _glass_back = assimp_pbr__plane((0, 1, 0, 1), z=-200, u_material_unlit=1.0)
        $ _glass_pixel = assimp_pbr__pixels(_glass, _glass_back, light_color=(0, 0, 0), environment=im.Null("#f00"))
        assert eval assimp_alpha__close(_glass_pixel, assimp_pbr__expected((1, 0, 0) if ior > 1.0 else (0, 1, 0)))

    testcase foreground_rejection:
        description "Refraction does not pull foreground objects into the glass from a neighboring screen pixel."

        $ _glass = Transform(GLTFModel("models/material-sided.gltf", shader="renpy.gltf.pbr", zoom=100, u_material_transmission_factor=1.0, u_material_thickness_factor=1.0, u_material_roughnessfactor=0.0), xpos=200, ypos=150, yrotate=45)
        $ _glass_back = Transform(GLTFModel("models/material-refraction-background.gltf", shader="renpy.gltf.pbr", zoom=200), xpos=200, ypos=150, zpos=-200)
        $ _glass_front = Transform(GLTFModel("models/material-sided.gltf", shader="renpy.gltf.pbr", zoom=100, u_color_base=(0, 1, 0, 1), u_material_unlit=1.0), xpos=85, ypos=150, zpos=80)
        $ _glass_pixel = assimp_pbr__pixels(_glass, _glass_back, _glass_front, light_color=(0, 0, 0))
        assert eval _glass_pixel[2] > 180 and _glass_pixel[0] < 3 and _glass_pixel[1] < 3

    testcase environment_fallback:
        description "Glass sees environment lighting where its transmitted background has no scene coverage."

        $ _glass_pixel = assimp_pbr__pixels(assimp_pbr__plane(u_material_transmission_factor=1.0, u_material_ior=1.0), light_color=(0, 0, 0), environment=im.Null("#0f0"))
        assert eval assimp_alpha__close(_glass_pixel, assimp_pbr__expected((0, 1, 0)))

testsuite assimp_peeling:

    testcase intersecting_alpha:
        parameter reverse = [False, True]
        parameter layers = [2, 4]
        description "Intersecting panes order per pixel, independently of their mesh centers and submission order."

        $ _peel_red = Transform(assimp_pbr__plane((1, 0, 0, 0.5), u_material_gltf_alphamode=2.0, u_material_unlit=1.0).child, xpos=200, ypos=150, yrotate=45)
        $ _peel_blue = Transform(assimp_pbr__plane((0, 0, 1, 0.5), u_material_gltf_alphamode=2.0, u_material_unlit=1.0).child, xpos=200, ypos=150, yrotate=-45)
        $ _peel_pixels = assimp_alpha__pixels(GLTFScene(assimp_alpha__camera(*([_peel_blue, _peel_red] if reverse else [_peel_red, _peel_blue])), light_color=(0, 0, 0), transparency_layers=layers), ((170, 150), (230, 150)))
        assert eval assimp_alpha__close(_peel_pixels[0], assimp_pbr__expected((2/3, 0, 1/3), alpha=0.75))
        assert eval assimp_alpha__close(_peel_pixels[1], assimp_pbr__expected((1/3, 0, 2/3), alpha=0.75))

    testcase opaque_occlusion:
        description "An opaque surface excludes transparent layers behind it while preserving the nearer layer."

        $ _peel_front = assimp_pbr__plane((1, 0, 0, 0.5), z=50, u_material_gltf_alphamode=2.0, u_material_unlit=1.0)
        $ _peel_back = assimp_pbr__plane((0, 1, 0, 0.5), z=-50, u_material_gltf_alphamode=2.0, u_material_unlit=1.0)
        $ _peel_opaque = assimp_pbr__plane((0, 0, 1, 1), u_material_unlit=1.0)
        $ _peel_pixel = assimp_pbr__pixels(_peel_back, _peel_front, _peel_opaque, light_color=(0, 0, 0), transparency_layers=4)
        assert eval assimp_alpha__close(_peel_pixel, assimp_pbr__expected((0.5, 0, 0.5)))

    testcase nearest_layer_cap:
        description "A one-layer budget keeps the nearest transparent surface and explicitly omits deeper layers."

        $ _peel_front = assimp_pbr__plane((1, 0, 0, 0.5), z=50, u_material_gltf_alphamode=2.0, u_material_unlit=1.0)
        $ _peel_back = assimp_pbr__plane((0, 0, 1, 0.5), z=-50, u_material_gltf_alphamode=2.0, u_material_unlit=1.0)
        $ _peel_pixel = assimp_pbr__pixels(_peel_back, _peel_front, light_color=(0, 0, 0), transparency_layers=1)
        assert eval assimp_alpha__close(_peel_pixel, assimp_pbr__expected((1, 0, 0), alpha=0.5))

    testcase layered_refraction:
        parameter layers = [2, 4]
        description "Two glass surfaces shade against completed farther HDR layers, retaining Beer absorption and depth."

        $ _peel_glass = assimp_pbr__plane(u_material_transmission_factor=1.0, u_material_ior=1.0, u_material_thickness_factor=0.5, u_material_attenuation_distance=0.5, u_material_attenuation_color=(0.25, 0.5, 1))
        $ _peel_second = assimp_pbr__plane(z=-20, u_material_transmission_factor=1.0, u_material_ior=1.0)
        $ _peel_back = assimp_pbr__plane(z=-100, u_material_emissivefactor=(1, 1, 1))
        $ _peel_pixel = assimp_pbr__pixels(_peel_glass, _peel_back, _peel_second, light_color=(0, 0, 0), meters_per_unit=0.01, transparency_layers=layers)
        assert eval assimp_alpha__close(_peel_pixel, assimp_pbr__expected((0.25, 0.5, 1)))

    testcase retained_glass_depth:
        parameter focus = [5.0, 7.0]
        description "Peeling retains the nearest glass depth for DoF instead of the opaque background's depth."

        $ _peel_glass = assimp_pbr__plane(u_material_transmission_factor=1.0, u_material_ior=1.0, u_material_roughnessfactor=0.0)
        $ _peel_back = Transform(GLTFModel("models/material-refraction-checker.gltf", shader="renpy.gltf.pbr", zoom=200), xpos=200, ypos=150, zpos=-200)
        $ _peel_scene = GLTFScene(assimp_alpha__camera(_peel_glass, _peel_back), light_color=(0, 0, 0), meters_per_unit=0.01, transparency_layers=4, depth_output=True)
        $ _peel_pixel = assimp_alpha__pixels(GLTFDepthOfField(_peel_scene, focus_distance=focus, max_blur=8), ((200, 150),))[0]
        assert eval (min(_peel_pixel[0], _peel_pixel[2]) < 3 if focus == 5.0 else min(_peel_pixel[0], _peel_pixel[2]) > 30)

testsuite assimp_caustics:

    after testcase:
        $ renpy.display.draw.set_gltf_profiling(False)
        if not screen "main_menu":
            run MainMenu(confirm=False, save=False)

    testcase identity_pane:
        parameter kind = ["directional", "point", "spot"]
        description "An index-one tinted pane moves no flux: enabling caustics preserves its existing colored shadow."

        $ _caustic_light = GLTFLight(kind, position=(400, 150, 200), direction=(-1, 0, -1), intensity=3.14159265359, outer_cone_angle=1.0)
        $ _caustic_pane = Transform(GLTFModel("models/alpha-opaque.gltf", shader="renpy.gltf.pbr", zoom=30, u_tex_base_color=im.Null("#fff"), u_color_base=(.6, .8, 1, 1), u_material_transmission_factor=1.0, u_material_metallicfactor=0.0, u_material_ior=1.0, u_material_roughnessfactor=0.0), xpos=250, ypos=150, zpos=50)
        $ _caustic_plain = assimp_pbr__pixels(assimp_pbr__plane(), _caustic_pane, light_color=(0, 0, 0), lights=(_caustic_light,), meters_per_unit=.01, colored_shadows=True, shadow_size=512)
        $ _caustic_active = assimp_pbr__pixels(assimp_pbr__plane(), _caustic_pane, light_color=(0, 0, 0), lights=(_caustic_light,), meters_per_unit=.01, caustics=True, shadow_size=512)
        assert eval assimp_alpha__close(_caustic_active, _caustic_plain)

    testcase focusing_lens:
        parameter kind = ["directional", "point", "spot"]
        description "A curved normal field concentrates refracted light on the first receiver with finite bounded brightness."

        $ _caustic_light = GLTFLight(kind, position=(400, 150, 200), direction=(-1, 0, -1), intensity=3.14159265359, outer_cone_angle=1.0)
        $ _caustic_lens = Transform(GLTFModel("models/material-caustic-lens.gltf", shader="renpy.gltf.pbr", zoom=20), xpos=250, ypos=150, zpos=50)
        $ _caustic_plain = assimp_pbr__pixels(assimp_pbr__plane(), _caustic_lens, light_color=(0, 0, 0), lights=(_caustic_light,), meters_per_unit=.01, colored_shadows=True, shadow_size=512)
        $ _caustic_active = assimp_pbr__pixels(assimp_pbr__plane(), _caustic_lens, light_color=(0, 0, 0), lights=(_caustic_light,), meters_per_unit=.01, caustics=True, shadow_size=512)
        $ _caustic_reference = assimp_pbr__pixels(assimp_pbr__plane(), light_color=(0, 0, 0), lights=(_caustic_light,), meters_per_unit=.01)
        $ _caustic_cap = assimp_pbr__tinted(_caustic_reference, (4, 4, 4), 1)
        assert eval _caustic_active[0] > _caustic_plain[0] + 10 and _caustic_active[0] <= _caustic_cap[0] + 2

    testcase rough_glass:
        description "Fully rough glass retains ordinary colored shadows without a focused caustic contribution."

        $ _caustic_lens = Transform(GLTFModel("models/material-caustic-lens.gltf", shader="renpy.gltf.pbr", zoom=20, u_material_roughnessfactor=1.0), xpos=250, ypos=150, zpos=50)
        $ _caustic_light = GLTFLight("directional", direction=(-1, 0, -1), intensity=3.14159265359)
        $ _caustic_plain = assimp_pbr__pixels(assimp_pbr__plane(), _caustic_lens, light_color=(0, 0, 0), lights=(_caustic_light,), meters_per_unit=.01, colored_shadows=True)
        $ _caustic_active = assimp_pbr__pixels(assimp_pbr__plane(), _caustic_lens, light_color=(0, 0, 0), lights=(_caustic_light,), meters_per_unit=.01, caustics=True)
        assert eval assimp_alpha__close(_caustic_active, _caustic_plain)

    testcase cache_and_pose_change:
        description "Unchanged caustics reuse GPU targets; a geometry change rebuilds the same image as a fresh uncached scene."

        $ renpy.display.draw.texture_loader.shadow_cache.clear()
        $ renpy.display.draw.set_gltf_profiling()
        $ _caustic_scene = assimp_caustic__scene()
        $ _caustic_before = assimp_shadow__sample(_caustic_scene, 0)
        $ renpy.display.draw.get_gltf_profile(reset=True)
        $ _caustic_again = assimp_shadow__sample(_caustic_scene, .1)
        $ _caustic_profile = renpy.display.draw.get_gltf_profile(reset=True)
        assert eval _caustic_before["hash"] == _caustic_again["hash"] and _caustic_profile["passes"].get("caustics", {}).get("draws", 0) == 0
        $ _caustic_scene.child = assimp_caustic__scene(zoom=30).child
        $ _caustic_changed = assimp_shadow__sample(_caustic_scene, .2)
        $ _caustic_fresh = assimp_shadow__sample(assimp_caustic__scene(zoom=30, shadow_cache=False), .2)
        assert eval _caustic_changed["hash"] == _caustic_fresh["hash"] and _caustic_changed["hash"] != _caustic_before["hash"]
        $ renpy.display.draw.set_gltf_profiling(False)

    testcase quality_combination:
        description "Caustics compose with cascades, offscreen refraction, peeling and retained DoF depth."

        $ _caustic_scene = assimp_caustic__scene(shadow_cascades=2, shadow_distance=8, refraction_margin=.25, transparency_layers=2, depth_output=True)
        $ _caustic_pixel = assimp_alpha__pixels(GLTFDepthOfField(_caustic_scene, focus_distance=5), ((200, 150),))[0]
        assert eval 20 < _caustic_pixel[0] < 250 and _caustic_pixel[3] == 255

    testcase save_load:
        description "Focused lighting renders identically after saving and restoring the scene without saving GPU targets."

        run Start("assimp_caustic_save")
        advance until "The focused light is ready."
        $ renpy.session["_caustic_hash"] = assimp_shadow__sample(assimp_alpha_saved_model, 0)["hash"]
        run Function(renpy.save, "assimp-caustic-test")
        $ assimp_alpha_saved_model = None
        run Function(renpy.load, "assimp-caustic-test")
        pause until "The focused light is ready."
        assert eval assimp_alpha_saved_model.caustics and assimp_alpha_saved_model.colored_shadows
        assert eval assimp_shadow__sample(assimp_alpha_saved_model, 0)["hash"] == renpy.session["_caustic_hash"]
        run Function(renpy.unlink_save, "assimp-caustic-test")

testsuite assimp_colored_shadows:

    testcase tint_and_absorption:
        parameter mode = ["tint", "volume", "blend"]
        parameter layers = [1, 2, 3]
        description "Light crossing glass retains tint and Beer absorption; partial coverage and the two-surface cap are explicit."

        $ _glass_light = GLTFLight("directional", direction=(-1, 0, -1), intensity=3.14159265359)
        $ _glass_reference = assimp_pbr__pixels(assimp_pbr__plane(), light_color=(0, 0, 0), lights=(_glass_light,), meters_per_unit=.01)
        $ _glass_casters = [Transform(GLTFModel("models/alpha-blend.gltf" if mode == "blend" else "models/alpha-opaque.gltf", shader="renpy.gltf.pbr", zoom=20, u_tex_base_color=im.Null("#fff"), u_color_base=(1, 1, 1, .5) if mode == "blend" else ((.25, .5, 1, 1) if mode == "tint" else (1, 1, 1, 1)), u_material_transmission_factor=0.0 if mode == "blend" else 1.0, u_material_metallicfactor=0.0, u_material_ior=1.0, u_material_thickness_factor=1.0 if mode == "volume" else 0.0, u_material_attenuation_color=(.25, .5, 1), u_material_attenuation_distance=.2), xpos=250 + 25 * i, ypos=150, zpos=50 + 25 * i) for i in range(layers)]
        $ _glass_pixel = assimp_pbr__pixels(assimp_pbr__plane(), *_glass_casters, light_color=(0, 0, 0), lights=(_glass_light,), meters_per_unit=.01, colored_shadows=True, shadow_size=512)
        $ _glass_expected = assimp_pbr__tinted(_glass_reference, (.5, .5, .5) if mode == "blend" else (.25, .5, 1), layers)
        assert eval assimp_alpha__close(_glass_pixel, _glass_expected)

    testcase opaque_occlusion_and_light_types:
        parameter kind = ["directional", "point", "spot"]
        parameter behind = [False, True]
        description "Colored layers use the matching projected or radial depth, and glass behind the receiver has no effect."

        $ _glass_light = GLTFLight(kind, position=(400, 150, 200), direction=(-1, 0, -1), intensity=3.14159265359, outer_cone_angle=1.0)
        $ _glass_caster = Transform(GLTFModel("models/alpha-opaque.gltf", shader="renpy.gltf.pbr", zoom=20, u_tex_base_color=im.Null("#fff"), u_color_base=(.1, .5, 1, 1), u_material_transmission_factor=1.0, u_material_metallicfactor=0.0, u_material_ior=1.0), xpos=150 if behind else 250, ypos=150, zpos=-50 if behind else 50)
        $ _glass_pixel = assimp_pbr__pixels(assimp_pbr__plane(), _glass_caster, light_color=(0, 0, 0), lights=(_glass_light,), meters_per_unit=.01, colored_shadows=True, shadow_size=512, shadow_cascades=2)
        assert eval (_glass_pixel[0] < _glass_pixel[1] < _glass_pixel[2] if not behind else max(_glass_pixel[:3]) - min(_glass_pixel[:3]) <= 1)

    testcase cache_color_change:
        description "A reused scene rebuilds colored shadows when only glass RGB changes, while unchanged frames reuse them."

        $ _glass_scene = assimp_shadow__scene()
        $ _glass_scene.colored_shadows = True
        $ _glass_scene.child = assimp_alpha__camera(assimp_pbr__plane(), Transform(GLTFModel("models/alpha-opaque.gltf", shader="renpy.gltf.pbr", zoom=20, u_tex_base_color=im.Null("#fff"), u_color_base=(.1, .5, 1, 1), u_material_metallicfactor=0.0, u_material_transmission_factor=1.0, u_material_ior=1.0), xpos=250, ypos=150, zpos=50))
        $ _glass_before = assimp_shadow__sample(_glass_scene, 0)
        $ _glass_again = assimp_shadow__sample(_glass_scene, .1)
        assert eval _glass_before["hash"] == _glass_again["hash"]
        $ _glass_scene.child = assimp_alpha__camera(assimp_pbr__plane(), Transform(GLTFModel("models/alpha-opaque.gltf", shader="renpy.gltf.pbr", zoom=20, u_tex_base_color=im.Null("#fff"), u_color_base=(1, .5, .1, 1), u_material_metallicfactor=0.0, u_material_transmission_factor=1.0, u_material_ior=1.0), xpos=250, ypos=150, zpos=50))
        $ _glass_after = assimp_shadow__sample(_glass_scene, .2)
        assert eval _glass_after["pixel"][0] > _glass_before["pixel"][0] and _glass_after["pixel"][2] < _glass_before["pixel"][2]

    testcase guarded_peeling:
        description "Colored shadow uniforms remain available in guarded HDR depth-peeling passes."

        $ _glass_caster = Transform(GLTFModel("models/alpha-opaque.gltf", shader="renpy.gltf.pbr", zoom=20, u_tex_base_color=im.Null("#fff"), u_color_base=(.1, .5, 1, 1), u_material_transmission_factor=1.0, u_material_metallicfactor=0.0, u_material_ior=1.0), xpos=250, ypos=150, zpos=50)
        $ _glass_pixel = assimp_pbr__pixels(assimp_pbr__plane(), _glass_caster, light_color=(0, 0, 0), lights=(GLTFLight("directional", direction=(-1, 0, -1), intensity=3.14159265359),), meters_per_unit=.01, colored_shadows=True, shadow_size=512, refraction_margin=.25, transparency_layers=2, shadow_cascades=2)
        assert eval _glass_pixel[0] < _glass_pixel[1] < _glass_pixel[2]

testsuite assimp_cascades:

    testcase directional_shadow:
        parameter cascades = [2, 4]
        parameter distance = [3.0, 20.0]
        description "Cascades shadow visible receivers inside the configured distance and leave farther receivers lit."

        $ _cascade_caster = Transform(GLTFModel("models/material-sided.gltf", shader="renpy.gltf.pbr", zoom=20), xpos=250, ypos=150, zpos=50)
        $ _cascade_pixel = assimp_pbr__pixels(assimp_pbr__plane(), _cascade_caster, light_color=(0, 0, 0), lights=(GLTFLight("directional", direction=(-1, 0, -1), intensity=3.14159265359),), meters_per_unit=0.01, shadow_size=1024, shadow_cascades=cascades, shadow_distance=distance)
        assert eval (_cascade_pixel[0] < 4 if distance > 5 else _cascade_pixel[0] > 30)

    testcase other_lights:
        parameter kind = ["point", "spot"]
        description "Point and spot shadow tiles retain their placement alongside a cascaded directional light."

        $ _cascade_caster = Transform(GLTFModel("models/material-sided.gltf", shader="renpy.gltf.pbr", zoom=20), xpos=250, ypos=150, zpos=50)
        $ _cascade_lights = (GLTFLight("directional", direction=(-1, 0, -1), color=(1, 0, 0), intensity=3.14159265359), GLTFLight(kind, position=(400, 150, 200), direction=(-1, 0, -1), intensity=3.14159265359, outer_cone_angle=1.0))
        $ _cascade_pixel = assimp_pbr__pixels(assimp_pbr__plane(), _cascade_caster, light_color=(0, 0, 0), lights=_cascade_lights, meters_per_unit=0.01, shadow_cascades=4, shadow_size=1024)
        assert eval max(_cascade_pixel[:3]) < 4

testsuite assimp_guard:

    testcase offscreen_background:
        parameter margin = [0.0, 0.25]
        parameter layers = [0, 4]
        description "A guard region supplies nearby offscreen geometry to a refracted ray that previously fell back to the environment."

        $ _guard_glass = Transform(GLTFModel("models/material-sided.gltf", shader="renpy.gltf.pbr", zoom=100, u_material_transmission_factor=1.0, u_material_ior=1.5, u_material_thickness_factor=2.0, u_material_roughnessfactor=0.0), xpos=20, ypos=150, yrotate=45)
        $ _guard_back = Transform(assimp_pbr__plane((0, 0, 0, 1), u_material_emissivefactor=(1, 0, 0)).child, xpos=200, ypos=150, zpos=-200, zoom=4)
        $ _guard_pixel = assimp_alpha__pixels(GLTFScene(assimp_alpha__camera(_guard_back, _guard_glass), light_color=(0, 0, 0), environment=im.Null("#00f"), refraction_margin=margin, transparency_layers=layers), ((20, 150),))[0]
        assert eval (_guard_pixel[0] > 170 and _guard_pixel[2] < 90 if margin else _guard_pixel[2] > 170 and _guard_pixel[0] < 3)

    testcase visible_framing:
        parameter camera = ["MainPerspective", "SquarePerspective", "MainOrthographic"]
        description "Expanded rendering preserves authored framing and transparent aspect bars in the visible output."

        $ _guard_plain = GLTFScene(assimp_camera__scene(camera), light_color=(0, 0, 0))
        $ _guard_expanded = GLTFScene(assimp_camera__scene(camera), light_color=(0, 0, 0), refraction_margin=0.25)
        assert eval assimp_shadow__sample(_guard_plain, 0)["hash"] == assimp_shadow__sample(_guard_expanded, 0)["hash"]

    testcase explicit_crop:
        description "An explicit crop remains a boundary when only the camera's sampling region expands."

        $ _guard_child = Fixed(Transform(assimp_camera__scene(), crop=(100, 50, 200, 200), xpos=100, ypos=50), xysize=(400, 300))
        $ _guard_plain = GLTFScene(_guard_child, light_color=(0, 0, 0))
        $ _guard_expanded = GLTFScene(_guard_child, light_color=(0, 0, 0), refraction_margin=0.25)
        assert eval assimp_shadow__sample(_guard_plain, 0)["hash"] == assimp_shadow__sample(_guard_expanded, 0)["hash"]

    testcase guarded_dof:
        parameter layers = [0, 4]
        parameter focus = [5.0, 7.0]
        description "Guard cropping preserves the actual camera depth and blur radius for the original visible frame."

        $ _guard_glass = assimp_pbr__plane(u_material_transmission_factor=1.0, u_material_ior=1.0)
        $ _guard_back = Transform(GLTFModel("models/material-refraction-checker.gltf", shader="renpy.gltf.pbr", zoom=200), xpos=200, ypos=150, zpos=-200)
        $ _guard_plain = GLTFScene(assimp_alpha__camera(_guard_back, _guard_glass), light_color=(0, 0, 0), meters_per_unit=0.01, depth_output=True, transparency_layers=layers)
        $ _guard_expanded = GLTFScene(assimp_alpha__camera(_guard_back, _guard_glass), light_color=(0, 0, 0), meters_per_unit=0.01, depth_output=True, transparency_layers=layers, refraction_margin=0.25)
        assert eval assimp_guard__matching(GLTFDepthOfField(_guard_plain, focus_distance=focus), GLTFDepthOfField(_guard_expanded, focus_distance=focus))

testsuite assimp_layered_material:

    testcase authored_maps:
        parameter mode = [0, 1, 2]
        description "Imported extension textures use the specified channels, sRGB color decoding, and linear alpha/data."

        $ _layered_model = Transform(GLTFModel("models/material-layers.gltf", material=True, shader="assimp_layers.data", zoom=100, u_layers_test=float(mode)), xpos=200, ypos=150)
        $ _layered_pixel = assimp_alpha__pixels(assimp_alpha__camera(_layered_model), ((200, 150),))[0]
        assert eval assimp_alpha__close(_layered_pixel, [(128, 128, 64, 255), (55, 55, 128, 255), (128, 64, 32, 255)][mode])

    testcase specular_weight:
        parameter factor = [0.0, 0.5, 1.0]
        description "Dielectric specular weight changes reflection energy while keeping a pure diffuse endpoint."

        $ _layered_pixel = assimp_pbr__pixels(assimp_pbr__plane(u_material_specular_factor=factor), light_direction=(0, 0, 1), light_color=(3.14159265359,) * 3)
        assert eval assimp_alpha__close(_layered_pixel, assimp_pbr__expected((1 - 0.03 * factor,) * 3))

    testcase metallic_specular:
        description "The dielectric specular extension does not suppress a metal's reflection."

        $ _layered_pixel = assimp_pbr__pixels(assimp_pbr__plane(u_material_specular_factor=0.0, u_material_metallicfactor=1.0, u_material_roughnessfactor=0.0), light_color=(0, 0, 0), environment=im.Null("#f00"))
        assert eval assimp_alpha__close(_layered_pixel, assimp_pbr__expected((1, 0, 0)))

    testcase coat_over_emission:
        parameter factor = [0.0, 0.5, 1.0]
        description "A clearcoat layer attenuates underlying emission by its view Fresnel weight."

        $ _layered_pixel = assimp_pbr__pixels(assimp_pbr__plane(u_material_clearcoat_factor=factor, u_material_emissivefactor=(4, 1, 0.25)), light_color=(0, 0, 0))
        assert eval assimp_alpha__close(_layered_pixel, assimp_pbr__expected(tuple(value * (1 - 0.04 * factor) for value in (4, 1, 0.25))))

    testcase coat_reflection:
        description "A clearcoat retains its own dielectric reflection when the underlying specular lobe is disabled."

        $ _layered_pixel = assimp_pbr__pixels(assimp_pbr__plane((0, 0, 0, 1), u_material_specular_factor=0.0, u_material_clearcoat_factor=1.0), light_color=(0, 0, 0), environment=im.Null("#fff"))
        assert eval assimp_alpha__close(_layered_pixel, assimp_pbr__expected((0.04,) * 3))

    testcase sheen_direct:
        description "A black substrate with zero dielectric specular receives the independently colored cloth lobe."

        $ _layered_plane = Transform(assimp_pbr__plane((0, 0, 0, 1), u_material_specular_factor=0.0, u_material_sheen_color_factor=(0, 0, 1), u_material_sheen_roughness_factor=1.0).child, xpos=200, ypos=150, yrotate=60)
        $ _layered_pixel = assimp_pbr__pixels(_layered_plane, light_direction=(0, 0, 1), light_color=(3.14159265359,) * 3)
        assert eval _layered_pixel[2] > 20 and _layered_pixel[0] < 3 and _layered_pixel[1] < 3

    testcase combined_layers:
        description "Specular, sheen, and clearcoat coexist, with clearcoat above emission and cloth shading."

        $ _layered_pixel = assimp_pbr__pixels(assimp_pbr__plane((0, 0, 0, 1), u_material_specular_factor=0.0, u_material_clearcoat_factor=1.0, u_material_sheen_color_factor=(0.5, 0.5, 0.5), u_material_sheen_roughness_factor=0.5, u_material_emissivefactor=(1, 0, 0)), light_color=(0, 0, 0))
        assert eval assimp_alpha__close(_layered_pixel, assimp_pbr__expected((0.96, 0, 0)))

testsuite assimp_reflection:

    after testcase:
        if not screen "main_menu":
            run MainMenu(confirm=False, save=False)

    testcase local_probe:
        parameter quality = ["low", "medium", "high"]
        description "A local probe replaces global reflection inside its bounds at every filtering quality."

        $ _probe = GLTFReflectionProbe(im.Null("#f00"), position=(200, 150, 0), box_min=(100, 50, -100), box_max=(300, 250, 100))
        $ _probe_pixel = assimp_pbr__pixels(assimp_pbr__plane(u_material_metallicfactor=1.0, u_material_roughnessfactor=0.0), light_color=(0, 0, 0), environment=im.Null("#00f"), environment_quality=quality, reflection_probes=(_probe,))
        assert eval assimp_alpha__close(_probe_pixel, assimp_pbr__expected((1, 0, 0)))

    testcase probe_fade:
        parameter fade = [0, 200]
        description "Box-edge distance fades local light into the global environment without multiplying its energy."

        $ _probe = GLTFReflectionProbe(im.Null("#f00"), position=(200, 150, 0), box_min=(100, 50, -100), box_max=(300, 250, 100), fade=fade)
        $ _probe_pixel = assimp_pbr__pixels(assimp_pbr__plane(u_material_metallicfactor=1.0, u_material_roughnessfactor=0.0), light_color=(0, 0, 0), environment=im.Null("#00f"), reflection_probes=(_probe,))
        assert eval assimp_alpha__close(_probe_pixel, assimp_pbr__expected((0.5, 0, 0.5) if fade else (1, 0, 0)))

    testcase probe_overlap:
        description "Two overlapping probes share one environment sampler and blend with normalized weights."

        $ _probe_a = GLTFReflectionProbe(im.Null("#f00"), position=(200, 150, 0), box_min=(100, 50, -100), box_max=(300, 250, 100))
        $ _probe_b = GLTFReflectionProbe(im.Null("#0f0"), position=(200, 150, 0), box_min=(100, 50, -100), box_max=(300, 250, 100))
        $ _probe_pixel = assimp_pbr__pixels(assimp_pbr__plane(u_material_metallicfactor=1.0, u_material_roughnessfactor=0.0), light_color=(0, 0, 0), reflection_probes=(_probe_a, _probe_b))
        assert eval assimp_alpha__close(_probe_pixel, assimp_pbr__expected((0.5, 0.5, 0)))

    testcase outside_probe:
        description "A probe contributes no reflection outside its scene-coordinate volume."

        $ _probe = GLTFReflectionProbe(im.Null("#f00"), position=(600, 150, 0), box_min=(500, 50, -100), box_max=(700, 250, 100))
        $ _probe_pixel = assimp_pbr__pixels(assimp_pbr__plane(u_material_metallicfactor=1.0, u_material_roughnessfactor=0.0), light_color=(0, 0, 0), environment=im.Null("#00f"), reflection_probes=(_probe,))
        assert eval assimp_alpha__close(_probe_pixel, assimp_pbr__expected((0, 0, 1)))

    testcase cached_probe:
        description "Moving the camera reuses filtered maps and atlas storage; repeated frames retain identical pixels."

        $ _probe = GLTFReflectionProbe(im.Null("#f00"), position=(200, 150, 0), box_min=(100, 50, -100), box_max=(300, 250, 100))
        $ _probe_scene = GLTFScene(assimp_alpha__camera(assimp_pbr__plane(u_material_metallicfactor=1.0, u_material_roughnessfactor=0.0)), light_color=(0, 0, 0), reflection_probes=(_probe,))
        $ _probe_before = assimp_shadow__sample(_probe_scene, 0)
        $ _probe_stats = renpy.display.draw.texture_loader.environment_cache.snapshot()
        $ _probe_after = assimp_shadow__sample(_probe_scene, 1)
        assert eval _probe_after["hash"] == _probe_before["hash"]
        assert eval renpy.display.draw.texture_loader.environment_cache.snapshot()["hits"] > _probe_stats["hits"]
        assert eval renpy.display.draw.texture_loader.environment_cache.snapshot()["retained_bytes"] <= 64 * 1024 * 1024

    testcase box_parallax:
        parameter capture_x = [200, 100]
        description "An off-center reflection ray hits the box wall before looking up the probe's captured direction."

        $ _probe = GLTFReflectionProbe("models/material-environment.png", position=(capture_x, 150, 0), box_min=(-100, 50, -100), box_max=(500, 250, 100))
        $ _probe_pixel = assimp_pbr__pixels(assimp_pbr__plane(u_material_metallicfactor=1.0, u_material_roughnessfactor=0.0), light_color=(0, 0, 0), reflection_probes=(_probe,))
        assert eval assimp_alpha__close(_probe_pixel, assimp_pbr__expected((1, 0, 0) if capture_x == 200 else (0, 0, 1)))

    testcase probe_rotation:
        parameter rotation = [0, 180]
        description "A local map has its own rotation, independent of the global environment."

        $ _probe = GLTFReflectionProbe("models/material-environment.png", position=(200, 150, 0), box_min=(100, 50, -100), box_max=(300, 250, 100), rotation=rotation)
        $ _probe_pixel = assimp_pbr__pixels(assimp_pbr__plane(u_material_metallicfactor=1.0, u_material_roughnessfactor=0.0), light_color=(0, 0, 0), environment_rotation=90, reflection_probes=(_probe,))
        assert eval assimp_alpha__close(_probe_pixel, assimp_pbr__expected((1, 0, 0) if not rotation else (0, 0, 1)))

    testcase save_load:
        description "Layered materials and local map graphs survive real saving, loading, and rollback without GPU cache state."

        run Start("assimp_probe_save")
        advance until "Reflection probe ready."
        $ renpy.session["_gltf_probe_hash"] = assimp_shadow__sample(assimp_alpha_saved_model, 0)["hash"]
        run Function(renpy.save, "assimp-probe-test")
        advance until "Reflection probe changed."
        run Rollback()
        pause until "Reflection probe ready."
        assert eval assimp_shadow__sample(assimp_alpha_saved_model, 0)["hash"] == renpy.session["_gltf_probe_hash"]
        run Function(renpy.load, "assimp-probe-test")
        pause until "Reflection probe ready."
        assert eval assimp_shadow__sample(assimp_alpha_saved_model, 0)["hash"] == renpy.session["_gltf_probe_hash"]
        run Function(renpy.unlink_save, "assimp-probe-test")
        $ renpy.session.pop("_gltf_probe_hash")

testsuite assimp_deformation:

    after testcase:
        run Hide("assimp_scene_live")
        if not screen "main_menu":
            run MainMenu(confirm=False, save=False)

    testcase live_playback:
        description "A visible morph clip redraws through the interaction loop until it reaches its final pose."

        run Show("assimp_scene_live", model=assimp_deform__scene(animation="Expand", animation_speed=20))
        pause 0.2
        assert eval assimp_alpha__close(assimp_scene__screen_pixel(270), (255, 0, 0, 255))
        assert eval assimp_alpha__close(assimp_scene__screen_pixel(170), (0, 0, 0, 255))

    testcase skeletal_pose:
        parameter time = [0, 2]
        description "Joint indices animate a skinned pane despite duplicate joint names; the mesh node's own translation is ignored."

        $ _deform_pixels = assimp_alpha__pixels(assimp_deform__scene(animation="Bend", animation_time=time), ((170, 150), (230, 150), (290, 150)))
        assert eval _deform_pixels[:2] == [(255, 0, 0, 255)] * 2
        assert eval _deform_pixels[2] == ((255, 0, 0, 255) if time else (0, 0, 0, 0))

    testcase morph_pose:
        parameter time = [0, 2]
        description "A SCALAR weight animation moves the intended target while leaving the other target at zero."

        $ _deform_pixels = assimp_alpha__pixels(assimp_deform__scene(animation="Expand", animation_time=time), ((170, 150), (230, 150), (270, 150)))
        assert eval _deform_pixels[0] == ((0, 0, 0, 0) if time else (255, 0, 0, 255))
        assert eval _deform_pixels[1] == (255, 0, 0, 255)
        assert eval _deform_pixels[2] == ((255, 0, 0, 255) if time else (0, 0, 0, 0))

    testcase blended_pose:
        description "Blending independent skeletal and morph clips matches an explicit half-weight pose."

        $ _deform_blended = assimp_deform__scene(animation="Bend", animation_time=2, animation_blend=("Expand", 0.5))
        $ _deform_reference = assimp_deform__scene(node_overrides={4: {"translation": (0.4, 0, 0)}, "Pane": {"weights": (0.5, 0)}})
        assert eval assimp_shadow__sample(_deform_blended, 0)["hash"] == assimp_shadow__sample(_deform_reference, 0)["hash"]

    testcase crossfade:
        parameter time = [0.0, 1.0, 2.0]
        description "A timed crossfade advances with shown time even when both clips have a pinned animation pose."

        $ _deform_blended = assimp_deform__scene(animation="Bend", animation_time=2, animation_blend=("Expand", 1.0), animation_blend_duration=2)
        $ _deform_reference = assimp_deform__scene(animation="Bend", animation_time=2, animation_blend=("Expand", time / 2))
        assert eval assimp_shadow__sample(_deform_blended, time)["hash"] == assimp_shadow__sample(_deform_reference, time)["hash"]

    testcase tangent_normal_morph:
        parameter tangent = [False, True]
        description "Normal-only and tangent-only targets update the tangent frame without relying on vendor morph support."

        $ _deform_scene = assimp_deform__scene(shader="assimp_deform.frame", u_deform_frame=float(tangent), node_overrides={"Pane": {"weights": (1, 0) if tangent else (0, 1)}})
        $ _deform_pixel = assimp_alpha__pixels(_deform_scene, ((230, 150),))[0]
        assert eval assimp_alpha__close(_deform_pixel, (218, 218, 128, 255) if tangent else (128, 242, 185, 255))

    testcase nonuniform_skin_normal:
        description "Normals use the inverse transpose of the blended skin matrix under nonuniform joint scale."

        $ _deform_scene = assimp_deform__scene(shader="assimp_deform.frame", u_deform_frame=0.0, node_overrides={3: {"scale": (1, 2, 1)}, 4: {"scale": (1, 2, 1)}, "Pane": {"weights": (0, 1)}})
        $ _deform_pixel = assimp_alpha__pixels(_deform_scene, ((200, 150),))[0]
        assert eval assimp_alpha__close(_deform_pixel, (128, 218, 218, 255))

    testcase skeleton_scale:
        parameter scale = [-1.0, 0.0, 1.0]
        description "Skeleton scale controls skinned winding; a collapsed rig renders no geometry without inverting a singular matrix."

        $ _deform_pixel = assimp_alpha__pixels(assimp_deform__scene(node_overrides={"Rig": {"scale": (scale, scale if not scale else 1, scale if not scale else 1)}}), ((200, 150),))[0]
        assert eval _deform_pixel == ((255, 0, 0, 255) if scale else (0, 0, 0, 0))

    testcase instance_isolation:
        description "Shared imports retain independent live poses and never mutate an earlier published frame."

        $ _deform_first = assimp_deform__scene(animation="Bend", animation_time=2)
        $ _deform_second = assimp_deform__scene(animation="Expand", animation_time=2)
        $ _deform_before = assimp_shadow__sample(_deform_first, 0)["hash"]
        $ _deform_other = assimp_shadow__sample(_deform_second, 0)["hash"]
        assert eval assimp_shadow__sample(_deform_first, 1)["hash"] == _deform_before
        assert eval _deform_other != _deform_before

    testcase deformed_shadow:
        parameter kind = ["skin", "morph"]
        parameter moved = [False, True]
        description "Both skin and morph deformation move actual shadow geometry and update its cached bounds."

        $ _deform_overrides = {3: {"translation": (0.4 if moved else 0, 0, 0)}, 4: {"translation": (0.4 if moved else 0, 0, 0)}} if kind == "skin" else {"Pane": {"weights": (1 if moved else 0, 0)}}
        $ _deform_caster = Transform(GLTFModel("models/scene-deformation.gltf", scene=True, zoom=20, node_overrides=_deform_overrides), xpos=252, ypos=150, zpos=50)
        $ _deform_pixel = assimp_pbr__pixels(assimp_pbr__plane(), _deform_caster, import_lights=False, light_color=(0, 0, 0), lights=(GLTFLight("directional", direction=(-1, 0, -1), intensity=3.14159265359),), shadow_size=512)
        assert eval (_deform_pixel[0] > 30 if moved else _deform_pixel[0] < 4)

    testcase save_load_rollback:
        description "Saved blended poses rebuild their native meshes after loading; rollback restores the earlier visible deformation."

        run Start("assimp_deform_save")
        advance until "Deformation ready."
        pause 0.2
        assert eval assimp_alpha__close(assimp_scene__screen_pixel(270), (255, 0, 0, 255))
        run Function(renpy.save, "assimp-deform-test")
        advance until "Deformation changed."
        run Rollback()
        pause until "Deformation ready."
        assert eval assimp_alpha__close(assimp_scene__screen_pixel(270), (255, 0, 0, 255))
        assert eval assimp_alpha__close(assimp_scene__screen_pixel(170), (0, 0, 0, 255))
        run Function(renpy.load, "assimp-deform-test")
        pause until "Deformation ready."
        assert eval assimp_alpha__close(assimp_scene__screen_pixel(270), (255, 0, 0, 255))
        assert eval assimp_alpha__close(assimp_scene__screen_pixel(170), (0, 0, 0, 255))
        run Function(renpy.unlink_save, "assimp-deform-test")

    testcase save_active_morph:
        description "Loading an active morph clip restores elapsed time instead of restarting vertex deformation."

        run Start("assimp_deform_timed_save")
        advance until "Deformation moving."
        pause 1.0
        run Function(renpy.save, "assimp-deform-timed-test")
        run Function(renpy.load, "assimp-deform-timed-test")
        pause until "Deformation moving."
        pause 0.05
        assert eval assimp_alpha__close(assimp_scene__screen_pixel(245), (255, 0, 0, 255))
        assert eval assimp_alpha__close(assimp_scene__screen_pixel(165), (0, 0, 0, 255))
        run Function(renpy.unlink_save, "assimp-deform-timed-test")

testsuite assimp_camera:

    after testcase:
        run Hide("assimp_scene_live")
        $ renpy.session.pop("_gltf_camera_hash", None)
        if not screen "main_menu":
            run MainMenu(confirm=False, save=False)

    testcase authored_projection:
        parameter camera = ["MainOrthographic", "MainPerspective", "SquarePerspective"]
        description "Authored perspective and orthographic cameras frame source-coordinate geometry without manual offsets."

        $ _camera_pixels = assimp_alpha__pixels(assimp_camera__scene(camera), ((140, 150), (260, 150)))
        assert eval assimp_alpha__close(_camera_pixels[0], (255, 0, 0, 255))
        assert eval assimp_alpha__close(_camera_pixels[1], (0, 0, 255, 255))

    testcase aspect_bars:
        description "An authored square perspective camera fits the offered rectangle with transparent side bars."

        $ _camera_pixels = assimp_alpha__pixels(assimp_camera__scene("SquarePerspective"), ((10, 150), (390, 150)))
        assert eval _camera_pixels[0][3] == _camera_pixels[1][3] == 0

    testcase animated_camera:
        parameter time = [0.0, 1.0]
        description "A camera-only animation moves the view while source meshes retain their independent bind poses."

        $ _camera_pixels = assimp_alpha__pixels(assimp_camera__scene("MainPerspective", animation="CameraPan", animation_time=time), ((200, 150),))
        assert eval (assimp_alpha__close(_camera_pixels[0], (255, 0, 0, 255)) if time == 0 else _camera_pixels[0][3] == 0)

    testcase orthographic_lighting:
        parameter dof = [False, True]
        description "Orthographic PBR uses a camera-facing view vector and exports usable geometry depth for DoF."

        $ _camera_source = GLTFModel("models/scene-cameras.gltf", scene=True, zoom=100)
        $ _camera_plane = assimp_pbr__plane().child
        $ _camera_scene = GLTFScene(GLTFCamera(_camera_source, _camera_plane, camera="MainOrthographic"), light_direction=(0, 0, 1), light_color=(3.14159265359,) * 3, meters_per_unit=0.01, depth_output=dof)
        $ _camera_effect = GLTFDepthOfField(_camera_scene, focus_distance=5.0, max_blur=0) if dof else _camera_scene
        $ _camera_pixel = assimp_alpha__pixels(_camera_effect, ((200, 150),))[0]
        assert eval assimp_alpha__close(_camera_pixel, assimp_pbr__expected((0.97,) * 3))

    testcase live_camera:
        description "A camera-only source requests redraws even when the displayed geometry comes from a separate model."

        $ _camera_source = GLTFModel("models/scene-cameras.gltf", scene=True, zoom=100, animation="CameraPan", animation_speed=20)
        $ _camera_child = GLTFModel("models/scene-cameras.gltf", scene=True, zoom=100)
        $ _camera_live = Transform(GLTFCamera(_camera_source, _camera_child, camera="MainPerspective"), xysize=(400, 300))
        run Show("assimp_scene_live", model=_camera_live)
        pause 0.25
        $ _camera_pixel = assimp_scene__screen_pixel(200)
        assert eval assimp_alpha__close(_camera_pixel, (0, 0, 255, 255))

    testcase save_load:
        description "Saving restores a selected authored camera and rebuilds its view/projection from the source scene."

        run Start("assimp_camera_save")
        advance until "Imported camera ready."
        $ renpy.session["_gltf_camera_hash"] = assimp_shadow__sample(assimp_alpha_saved_model, 0)["hash"]
        run Function(renpy.save, "assimp-camera-test")
        $ assimp_alpha_saved_model = None
        run Function(renpy.load, "assimp-camera-test")
        pause until "Imported camera ready."
        assert eval assimp_alpha_saved_model.camera == "MainOrthographic"
        $ _camera_restored = assimp_shadow__sample(assimp_alpha_saved_model, 0)
        assert eval _camera_restored["hash"] == renpy.session["_gltf_camera_hash"]
        run Function(renpy.unlink_save, "assimp-camera-test")

testsuite assimp_integration:

    after testcase:
        $ renpy.session.pop("_gltf_integration_hash", None)
        if not screen "main_menu":
            run MainMenu(confirm=False, save=False)

    testcase baked_glass:
        parameter glass = [False, True]
        parameter blur = [None, 0.0, 8.0]
        description "Baked sRGB/depth output becomes opaque linear HDR, remains visible through glass, and tolerates DoF."

        $ _integrated = assimp_integration__scene(blur=blur, glass=glass)
        $ _integrated_pixel = assimp_alpha__pixels(_integrated, ((200, 150),))[0]
        $ _integrated_linear = tuple(value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4 for value in (0.25, 0.5, 0.75))
        assert eval assimp_alpha__close(_integrated_pixel, assimp_pbr__expected(_integrated_linear))

    testcase passthrough:
        parameter glass = [False, True]
        parameter blur = [None, 0.0, 8.0]
        description "Without tone mapping, baked sRGB leaves the scene at its source display values, with or without DoF."

        $ _integrated = assimp_integration__scene(blur=blur, glass=glass, tone_mapping=None)
        $ _integrated_pixel = assimp_alpha__pixels(_integrated, ((200, 150),))[0]
        assert eval assimp_alpha__close(_integrated_pixel, (64, 128, 191, 255))

    testcase scene_distance:
        description "A shader above a depth-output scene reads the baked plane's camera distance of 6 meters."

        $ _integrated = Transform(assimp_integration__scene(blur=0, glass=False).child, shader="assimp_integration.distance")
        $ _integrated_pixel = assimp_alpha__pixels(_integrated, ((200, 150),))[0]
        assert eval abs(_integrated_pixel[0] - 153) <= 2

    testcase glass_distance:
        parameter blend = [False, True]
        description "Opaque-mode glass reports its own 5 meter distance, while blend-mode glass leaves the 6 meter plane's."

        $ _integrated = Transform(assimp_integration__scene(blur=0, glass_blend=blend).child, shader="assimp_integration.distance")
        $ _integrated_pixel = assimp_alpha__pixels(_integrated, ((200, 150),))[0]
        assert eval abs(_integrated_pixel[0] - (153 if blend else 127)) <= 2

    testcase blended_glass_passthrough:
        description "Blend-mode glass still transmits the baked plane's source display values."

        $ _integrated = assimp_integration__scene(glass_blend=True, tone_mapping=None)
        $ _integrated_pixel = assimp_alpha__pixels(_integrated, ((200, 150),))[0]
        assert eval assimp_alpha__close(_integrated_pixel, (64, 128, 191, 255))

    testcase zero_blur:
        description "Disabling depth blur preserves the scene's ordinary tone-mapped output exactly."

        $ _integrated_plain = assimp_shadow__sample(assimp_integration__scene(), 0)
        $ _integrated_dof = assimp_shadow__sample(assimp_integration__scene(blur=0), 0)
        assert eval _integrated_plain["hash"] == _integrated_dof["hash"]

    testcase focus:
        parameter focused = [False, True]
        description "Perspective depth reconstruction keeps the focal plane sharp and blurs an out-of-focus checker."

        $ _integrated_background = Transform(GLTFModel("models/material-refraction-checker.gltf", shader="renpy.gltf.pbr", zoom=200), xpos=200, ypos=150, zpos=-100)
        $ _integrated_scene = GLTFScene(assimp_alpha__camera(_integrated_background), light_color=(0, 0, 0), depth_output=True, meters_per_unit=0.01)
        $ _integrated_dof = GLTFDepthOfField(_integrated_scene, focus_distance=6.0 if focused else 1.0, focus_range=0.2, max_blur=12)
        $ _integrated_pixel = assimp_alpha__pixels(_integrated_dof, ((200, 150),))[0]
        assert eval (max(_integrated_pixel[0], _integrated_pixel[2]) > 180 and min(_integrated_pixel[0], _integrated_pixel[2]) < 3 if focused else min(_integrated_pixel[0], _integrated_pixel[2]) > 30)

    testcase save_load:
        description "A saved baked/PBR/DoF graph reconstructs its depth targets and restores the rendered image."

        run Start("assimp_integration_save")
        advance until "Integrated room ready."
        $ renpy.session["_gltf_integration_hash"] = assimp_shadow__sample(assimp_alpha_saved_model, 0)["hash"]
        run Function(renpy.save, "assimp-integration-test")
        $ assimp_alpha_saved_model = None
        run Function(renpy.load, "assimp-integration-test")
        pause until "Integrated room ready."
        $ _integrated_after = assimp_shadow__sample(assimp_alpha_saved_model, 0)
        assert eval renpy.session["_gltf_integration_hash"] == _integrated_after["hash"]
        run Function(renpy.unlink_save, "assimp-integration-test")

    testcase rollback:
        description "Rollback restores the prior integrated scene after a transition to a different baked palette."

        run Start("assimp_integration_save")
        advance until "Integrated room ready."
        $ renpy.session["_gltf_integration_hash"] = assimp_shadow__sample(assimp_alpha_saved_model, 0)["hash"]
        advance until "Integrated room changed."
        run Rollback()
        pause until "Integrated room ready."
        $ _integrated_after = assimp_shadow__sample(assimp_alpha_saved_model, 0)
        assert eval renpy.session["_gltf_integration_hash"] == _integrated_after["hash"]

testsuite assimp_shadow_cache:

    before testcase:
        $ renpy.display.draw.texture_loader.shadow_cache.clear()
        $ renpy.display.draw.set_gltf_profiling()

    after testcase:
        $ renpy.display.draw.set_gltf_profiling(False)

    testcase reuse_and_invalidation:
        parameter kind = ["directional", "point", "spot"]
        parameter change = ["caster", "light", "alpha", "texture", "uv", "cutoff", "camera", "rgb"]
        description "Cached maps match a fresh render after camera, caster, light, cutout, UV, and material changes."

        $ _shadow_cached = assimp_shadow__scene(kind)
        $ _shadow_warm = assimp_shadow__sample(_shadow_cached, -0.1)
        $ _shadow_initial = assimp_shadow__sample(_shadow_cached, 0.0)
        $ renpy.display.draw.get_gltf_profile(reset=True)
        $ _shadow_reused = assimp_shadow__sample(_shadow_cached, 0.1)
        $ _shadow_profile = renpy.display.draw.get_gltf_profile(reset=True)
        assert eval _shadow_initial["hash"] == _shadow_reused["hash"]
        assert eval _shadow_profile["passes"].get("shadow", {}).get("draws", 0) == 0
        $ _shadow_changed = assimp_shadow__scene(kind, change)
        $ _shadow_cached.child = _shadow_changed.child
        $ _shadow_cached.lights = _shadow_changed.lights
        $ _shadow_result = assimp_shadow__sample(_shadow_cached, 0.2)
        $ _shadow_profile = renpy.display.draw.get_gltf_profile(reset=True)
        $ _shadow_reference = assimp_shadow__sample(assimp_shadow__scene(kind, change, cache=False), 0.2)
        assert eval _shadow_result["hash"] == _shadow_reference["hash"]
        $ _shadow_draws = sum(_shadow_profile["passes"].get(name, {}).get("draws", 0) for name in ("shadow", "shadow_resolve"))
        assert eval (_shadow_draws == 0 if change in ("camera", "rgb") else _shadow_draws > 0)

    testcase texture_reset:
        description "Evicting renderer textures drops retained maps and rebuilds the same shadow image."

        $ _shadow_cached = assimp_shadow__scene()
        $ _shadow_initial = assimp_shadow__sample(_shadow_cached, 0.0)
        $ renpy.display.interface.kill_textures()
        $ renpy.display.draw.get_gltf_profile(reset=True)
        $ _shadow_result = assimp_shadow__sample(_shadow_cached, 0.1)
        $ _shadow_profile = renpy.display.draw.get_gltf_profile()
        assert eval _shadow_result["hash"] == _shadow_initial["hash"]
        assert eval _shadow_profile["passes"]["shadow"]["draws"] > 0

testsuite assimp_scene:

    after testcase:
        run Hide("assimp_scene_live")
        if not screen "main_menu":
            run MainMenu(confirm=False, save=False)

    testcase live_redraw:
        description "A shown animated model requests redraws until its final pose, even without other animation."

        run Show("assimp_scene_live", model=assimp_scene__camera(animation="Slide", animation_speed=20))
        pause 0.2
        $ _scene_pixel = assimp_scene__screen_pixel(260)
        assert eval assimp_alpha__close(_scene_pixel, (255, 0, 0, 255))
        $ _scene_pixel = assimp_scene__screen_pixel(140)
        assert eval assimp_alpha__close(_scene_pixel, (0, 0, 0, 255))

    testcase bind_pose:
        description "Named scene import preserves static geometry, materials, and hierarchy placement."

        $ _scene_pixels = assimp_scene__pixels()
        assert eval assimp_alpha__close(_scene_pixels[0], (255, 0, 0, 255))
        assert eval _scene_pixels[1][3] == 0
        assert eval assimp_alpha__close(_scene_pixels[2], (0, 0, 255, 255))

    testcase named_visibility:
        parameter target = ["Door", "Room"]
        description "Visibility overrides affect the selected node and its descendants without hiding unrelated nodes."

        $ _scene_pixels = assimp_scene__pixels(node_overrides={target: {"visible": False}})
        assert eval _scene_pixels[0][3] == 0
        assert eval (_scene_pixels[2][3] == 0 if target == "Room" else _scene_pixels[2][2] == 255)

    testcase named_transform:
        description "A local node translation moves only that object, retaining its authored scale."

        $ _scene_pixels = assimp_scene__pixels(node_overrides={"Door": {"translation": (0, 0, 0.2)}})
        assert eval _scene_pixels[0][3] == 0
        assert eval assimp_alpha__close(_scene_pixels[1], (255, 0, 0, 255))
        assert eval assimp_alpha__close(_scene_pixels[2], (0, 0, 255, 255))

    testcase named_material:
        parameter texture = [False, True]
        description "Material overrides affect only the named material, including sampler-aware texture replacement."

        $ _scene_override = {"u_tex_base_color": im.Null("#0f0"), "u_color_base": (1, 1, 1, 1)} if texture else {"u_color_base": (0, 1, 0, 1)}
        $ _scene_pixels = assimp_scene__pixels(material_overrides={"DoorPaint": _scene_override})
        assert eval assimp_alpha__close(_scene_pixels[0], (0, 255, 0, 255))
        assert eval assimp_alpha__close(_scene_pixels[2], (0, 0, 255, 255))

    testcase automatic_playback:
        parameter time = [0.0, 1.0, 2.0, 3.0]
        description "The renderer evaluates node animation from shown-time, clamping at the final frame."

        $ _scene_pixels = assimp_scene__pixels(st=time, animation="Slide")
        assert eval assimp_alpha__close(_scene_pixels[min(int(time), 2)], (255, 0, 0, 255))
        assert eval (_scene_pixels[0][3] == 0 if time else _scene_pixels[1][3] == 0)

    testcase playback_controls:
        parameter mode = ["loop", "reverse", "fixed"]
        description "Looping, reverse speed, offset, and fixed-time poses select the expected middle frame."

        $ _scene_options = {"animation_loop": True} if mode == "loop" else {"animation_speed": -1, "animation_offset": 4} if mode == "reverse" else {"animation_time": 1.0}
        $ _scene_pixels = assimp_scene__pixels(st=3.0, animation="Slide", **_scene_options)
        assert eval assimp_alpha__close(_scene_pixels[1], (255, 0, 0, 255))

    testcase independent_instances:
        description "Two instances share imported geometry while keeping independent poses and material changes."

        $ _scene_first = Transform(assimp_scene__model(node_overrides={"Wall": {"visible": False}}), xpos=200, ypos=150)
        $ _scene_second = Transform(assimp_scene__model(animation="Slide", animation_time=1.0, node_overrides={"Wall": {"visible": False}}, material_overrides={"DoorPaint": {"u_color_base": (0, 1, 0, 1)}}), xpos=200, ypos=150)
        $ _scene_pixels = assimp_alpha__pixels(assimp_alpha__camera(_scene_first, _scene_second), ((140, 150), (200, 150)))
        assert eval assimp_alpha__close(_scene_pixels[0], (255, 0, 0, 255))
        assert eval assimp_alpha__close(_scene_pixels[1], (0, 255, 0, 255))

    testcase animated_shadow:
        parameter time = [0.0, 1.0]
        description "Animated node transforms also move shadow casters, independently of the camera."

        $ _scene_caster = Transform(assimp_scene__model(animation="Slide", animation_time=time, node_overrides={"Room": {"scale": (0.2, 0.2, 0.2)}, "Wall": {"visible": False}, "Lamp": {"visible": False}}), xpos=262, ypos=150, zpos=50)
        $ _scene_pixel = assimp_pbr__pixels(assimp_pbr__plane(), _scene_caster, import_lights=False, light_color=(0, 0, 0), lights=(GLTFLight("directional", direction=(-1, 0, -1), intensity=3.14159265359),))
        assert eval (_scene_pixel[0] < 4 if time == 0 else _scene_pixel[0] > 30)

    testcase animated_light:
        parameter time = [0.0, 1.0]
        description "An imported light follows its animated parent, including model placement and physical scene units."

        $ _scene_rig = Transform(assimp_scene__model(animation="Slide", animation_time=time, node_overrides={"Wall": {"visible": False}, "Lamp": {"translation": (0, 0, 0.8)}}, material_overrides={"DoorPaint": {"u_material_gltf_alphamode": 1.0, "u_color_base": (1, 0, 0, 0)}}), xpos=200, ypos=150)
        $ _scene_pixel = assimp_pbr__pixels(assimp_pbr__plane(), _scene_rig, light_color=(0, 0, 0), meters_per_unit=0.01)
        assert eval assimp_alpha__close(_scene_pixel, assimp_pbr__expected((0.97 * 4 / 3.14159265359 / (1 + (0.6 if time == 0 else 0) ** 2) ** 1.5,) * 3))

    testcase save_load:
        description "A saved scene instance restores its selected clip, fixed pose, and per-material overrides."

        run Start("assimp_scene_save")
        advance until "Door in the middle."
        run Function(renpy.save, "assimp-scene-test")
        $ assimp_alpha_saved_model = None
        run Function(renpy.load, "assimp-scene-test")
        pause until "Door in the middle."
        $ _scene_pixel = assimp_alpha__pixels(assimp_alpha_saved_model, ((200, 150),))[0]
        assert eval assimp_alpha__close(_scene_pixel, (0, 255, 0, 255))
        run Function(renpy.unlink_save, "assimp-scene-test")
        run MainMenu(confirm=False)

    testcase rollback:
        description "Rollback restores a prior instance's node visibility, material, and animation pose."

        run Start("assimp_scene_save")
        advance until "Door in the middle."
        advance until "Door hidden."
        $ _scene_pixel = assimp_alpha__pixels(assimp_alpha_saved_model, ((200, 150),))[0]
        assert eval _scene_pixel[3] == 0
        run Rollback()
        pause until "Door in the middle."
        $ _scene_pixel = assimp_alpha__pixels(assimp_alpha_saved_model, ((200, 150),))[0]
        assert eval assimp_alpha__close(_scene_pixel, (0, 255, 0, 255))
        run MainMenu(confirm=False)

    testcase save_active_playback:
        parameter variant = ["", "_hdr", "_alias", "_box"]
        description "Loading an actively playing scene restores its shown-time rather than restarting the clip at its first frame."

        run Start("assimp_scene_timed" + variant + "_save")
        advance until "Door moving."
        pause 3.0
        run Function(renpy.save, "assimp-scene-timed-test")
        pause 0.3
        run Function(renpy.load, "assimp-scene-timed-test")
        pause until "Door moving."
        pause 0.05
        $ _scene_pixel = assimp_scene__screen_pixel(200)
        assert eval assimp_alpha__close(_scene_pixel, assimp_pbr__expected((1, 0, 0)) if variant == "_hdr" else (255, 0, 0, 255))
        run Function(renpy.unlink_save, "assimp-scene-timed-test")
        run MainMenu(confirm=False)
