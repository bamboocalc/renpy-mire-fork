# Live GPU tests for 3D projective UI interaction and clickable GLTFModel displayables.

init python:

    _test_3d_clicks = []
    _test_3d_hovered = []
    _test_3d_vp_adj = None

    def _test_3d_reset():
        _test_3d_clicks.clear()
        _test_3d_hovered.clear()

    def _test_3d_record(tag):
        _test_3d_clicks.append(tag)

    def _test_3d_hover(tag):
        _test_3d_hovered.append(tag)

    def _test_3d_mon_matrix():
        cx = config.screen_width / 2.0
        cy = config.screen_height / 2.0
        return (
            Matrix.offset(cx, cy, -250.0)
            * Matrix.rotate(0.0, 35.0, 0.0)
            * Matrix.offset(-200.0, -150.0, 0.0)
        )

    def _test_3d_project_monitor(lx, ly, cam_x=0.0, cam_z=0.0, cam_yrot=0.0):
        w = float(config.screen_width)
        h = float(config.screen_height)
        view = (
            Matrix.offset(w / 2.0, h / 2.0, 1000.0)
            * Matrix.rotate(0.0, -cam_yrot, 0.0)
            * Matrix.offset(-w / 2.0, -h / 2.0, -1000.0)
            * Matrix.offset(-cam_x, 0.0, -cam_z)
        )
        total = Matrix.perspective(w, h, 100.0, 1000.0, 10000.0) * view * _test_3d_mon_matrix()
        px, py, _pz, pw = total.transform(lx, ly, 0.0, 1.0, components=4)
        return (int(round(px / pw)), int(round(py / pw)))

    def _test_3d_cam_view(content, use_gltf_scene=False, margin=0.0):
        cam = Transform(
            content,
            xysize=(config.screen_width, config.screen_height),
            perspective=(100.0, 1000.0, 10000.0),
            gl_depth=True,
        )
        if use_gltf_scene:
            return GLTFScene(
                cam,
                light_color=(0.0, 0.0, 0.0),
                import_lights=False,
                shadows=False,
                transmission=False,
                tone_mapping=None,
                refraction_margin=margin,
            )
        return cam


screen test_3d_monitor_screen(cam_x=0, cam_z=0.0, cam_yrot=0.0):
    $ _mon_mat = _test_3d_mon_matrix()
    fixed:
        xysize (config.screen_width, config.screen_height)
        at transform:
            perspective (100.0, 1000.0, 10000.0)
            gl_depth True
            xpos cam_x
            zpos cam_z
            yrotate cam_yrot

        fixed:
            xysize (400, 300)
            at transform:
                mesh True
                matrixtransform _mon_mat
                matrixanchor (0, 0)

            add Solid("#1e293b") xysize (400, 300)

            button:
                xpos 20 ypos 20 xysize (360, 80)
                background Solid("#2563eb")
                hover_background Solid("#3b82f6")
                action Function(_test_3d_record, "approve")
                text "Approve" align (0.5, 0.5)

            viewport:
                id "mon_vp"
                xpos 20 ypos 120 xysize (360, 120)
                mousewheel True
                draggable True
                yadjustment _test_3d_vp_adj
                vbox:
                    button:
                        xysize (360, 100)
                        background Solid("#0f766e")
                        action Function(_test_3d_record, "item1")
                        text "Item 1" align (0.5, 0.5)
                    button:
                        xysize (360, 100)
                        background Solid("#7c3aed")
                        action Function(_test_3d_record, "item2")
                        text "Item 2" align (0.5, 0.5)


screen test_3d_occlusion_screen(use_gltf_scene=False, prop_yrotate=0, prop_sensitive=True, margin=0.0):
    $ _cx = config.screen_width // 2
    $ _cy = config.screen_height // 2
    $ _mon_mat = Matrix.offset(_cx - 220, _cy - 120, -300.0)
    $ _box_model = GLTFModel("models/material-sided.gltf", zoom=60, material=True)
    $ _scene_content = Fixed(
        Button(
            Transform(
                _box_model,
                matrixtransform=Matrix.offset(_cx, _cy, 150.0) * Matrix.rotate(0.0, prop_yrotate, 0.0),
            ),
            style="empty",
            focus_mask=True,
            action=Function(_test_3d_record, "gltf_prop") if prop_sensitive else None,
            hovered=Function(_test_3d_hover, "gltf_prop") if prop_sensitive else None,
        ),
        Transform(
            Button(
                Solid("#1d4ed8", xysize=(440, 240)),
                style="empty",
                action=Function(_test_3d_record, "bg_monitor"),
            ),
            mesh=True,
            shader=("renpy.gltf.baked_srgb",) if use_gltf_scene else None,
            matrixtransform=_mon_mat,
            matrixanchor=(0, 0),
        ),
        xysize=(config.screen_width, config.screen_height),
    )

    add _test_3d_cam_view(_scene_content, use_gltf_scene=use_gltf_scene, margin=margin)


testsuite interaction_3d:

    testcase angled_monitor_buttons_and_viewport_click:
        description "Buttons and clipped scrollable viewports work on an angled 3D monitor."

        $ _test_3d_reset()
        $ _test_3d_vp_adj = ui.adjustment()
        run Show("test_3d_monitor_screen")
        assert screen "test_3d_monitor_screen"

        # Click projected center of Approve button (200, 60).
        $ _p_approve = _test_3d_project_monitor(200.0, 60.0)
        move pos _p_approve
        click pos _p_approve
        assert eval _test_3d_clicks == ["approve"]

        # Click projected center of visible Item 1 (200, 170).
        $ _p_item1 = _test_3d_project_monitor(200.0, 170.0)
        move pos _p_item1
        click pos _p_item1
        assert eval _test_3d_clicks == ["approve", "item1"]

        # Click projected center of Item 2 while scrolled outside viewport clip (200, 270).
        $ _p_clipped = _test_3d_project_monitor(200.0, 270.0)
        move pos _p_clipped
        click pos _p_clipped
        assert eval _test_3d_clicks == ["approve", "item1"]

        # Scroll down via mousewheel inside the angled 3D viewport, then click Item 2 at (200, 190).
        move pos _p_item1
        scroll amount 5 pos _p_item1
        pause 0.05
        assert eval _test_3d_vp_adj.value > 0
        $ _p_item2_visible = _test_3d_project_monitor(200.0, 190.0)
        move pos _p_item2_visible
        click pos _p_item2_visible
        assert eval _test_3d_clicks == ["approve", "item1", "item2"]

        run Hide("test_3d_monitor_screen")

    testcase gltf_model_button_and_depth_occlusion:
        description "A foreground GLTFModel button wins clicks over a background 2D monitor drawn after it."

        $ _test_3d_reset()
        run Show("test_3d_occlusion_screen", use_gltf_scene=False)
        assert screen "test_3d_occlusion_screen"

        $ _center = (config.screen_width // 2, config.screen_height // 2)
        move pos _center
        click pos _center
        assert eval "gltf_prop" in _test_3d_hovered
        assert eval _test_3d_clicks == ["gltf_prop"]

        # Click exposed left strip of the background monitor outside the 3D prop.
        $ _bg_left = (config.screen_width // 2 - 140, config.screen_height // 2)
        move pos _bg_left
        click pos _bg_left
        assert eval _test_3d_clicks == ["gltf_prop", "bg_monitor"]

        run Hide("test_3d_occlusion_screen")

        $ _test_3d_reset()
        run Show("test_3d_occlusion_screen", prop_yrotate=180)
        pause 0.05
        click pos _center
        assert eval _test_3d_clicks == ["bg_monitor"]
        run Hide("test_3d_occlusion_screen")

    testcase gltf_scene_preserves_3d_clicks:
        parameter margin = [0.0, 0.1]
        description "GLTFScene passes 3D clicks and depth occlusion through to GLTFModel and 3D monitor UI, with or without a refraction margin."

        $ _test_3d_reset()
        run Show("test_3d_occlusion_screen", use_gltf_scene=True, margin=margin)
        assert screen "test_3d_occlusion_screen"

        $ _center = (config.screen_width // 2, config.screen_height // 2)
        move pos _center
        click pos _center
        assert eval _test_3d_clicks == ["gltf_prop"]

        $ _bg_left = (config.screen_width // 2 - 140, config.screen_height // 2)
        move pos _bg_left
        click pos _bg_left
        assert eval _test_3d_clicks == ["gltf_prop", "bg_monitor"]

        run Hide("test_3d_occlusion_screen")

    testcase moved_rotated_camera_and_insensitive_occluder:
        description "A panned/rotated 3D camera keeps monitor clicks and viewport scrolling aligned, and an insensitive foreground GLTFModel still occludes."

        $ _test_3d_reset()
        $ _test_3d_vp_adj = ui.adjustment()
        run Show("test_3d_monitor_screen", cam_x=80, cam_z=-120.0, cam_yrot=-10.0)
        pause 0.05

        $ _p_approve = _test_3d_project_monitor(200.0, 60.0, cam_x=80, cam_z=-120.0, cam_yrot=-10.0)
        move pos _p_approve
        click pos _p_approve
        assert eval (_test_3d_clicks == ["approve"])

        $ _p_item1 = _test_3d_project_monitor(200.0, 170.0, cam_x=80, cam_z=-120.0, cam_yrot=-10.0)
        move pos _p_item1
        scroll amount 5 pos _p_item1
        pause 0.05
        assert eval (_test_3d_vp_adj.value > 0)
        $ _p_item2 = _test_3d_project_monitor(200.0, 190.0, cam_x=80, cam_z=-120.0, cam_yrot=-10.0)
        move pos _p_item2
        click pos _p_item2
        assert eval (_test_3d_clicks == ["approve", "item2"])

        run Hide("test_3d_monitor_screen")

        # Insensitive foreground GLTFModel still blocks clicks to the background monitor behind it.
        $ _test_3d_reset()
        run Show("test_3d_occlusion_screen", use_gltf_scene=False, prop_sensitive=False)
        pause 0.05
        $ _center = (config.screen_width // 2, config.screen_height // 2)
        move pos _center
        click pos _center
        assert eval (_test_3d_clicks == [])
        $ _bg_left = (config.screen_width // 2 - 140, config.screen_height // 2)
        move pos _bg_left
        click pos _bg_left
        assert eval (_test_3d_clicks == ["bg_monitor"])

        run Hide("test_3d_occlusion_screen")
