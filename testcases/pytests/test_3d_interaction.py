from types import SimpleNamespace

import pytest

import renpy
from renpy.display.matrix import Matrix
from renpy.display.render import IDENTITY, Render, unproject_matrix
from renpy.gl2.gl2mesh3 import Mesh3

MATRIX_VIEW = 0
MATRIX_PROJECTION = 2


def _make_perspective_root(child, width=800, height=600, near=100.0, z11=1000.0, far=10000.0,
                           view=None, depth=True):
    """Builds a minimal perspective Render tree matching RenderTransform.render()."""

    rv = Render(width, height)
    rv.matrix_kind = MATRIX_VIEW
    if view is not None:
        rv.reverse = view
        rv.forward = view.inverse()
    rv.blit(child, (0, 0))

    prv = Render(width, height)
    prv.matrix_kind = MATRIX_PROJECTION
    prv.reverse = Matrix.perspective(width, height, near, z11, far)
    prv.forward = prv.reverse.inverse()
    if depth:
        prv.add_property("depth", True)
    prv.blit(rv, (0, 0))
    return prv


def _project_point(matrix, x, y, z=0.0):
    px, py, pz, pw = matrix.transform(x, y, z, 1.0, components=4)
    assert pw > 0.0
    return (px / pw, py / pw, pz / pw)


def _make_mesh3_quad(x0, y0, x1, y1):
    """Creates a two-triangle Mesh3 quad on z=0 with explicit bounds."""

    mesh = Mesh3.rectangle(x0, y0, x1, y1)
    mesh.bounds = (min(x0, x1), min(y0, y1), 0.0, max(x0, x1), max(y0, y1), 0.0)
    return mesh


def _event_transform(child, perspective=None):
    """Build a transform with the state normally supplied by its last render."""
    from renpy.display.transform import Transform, TransformState

    tf = Transform.__new__(Transform)
    tf.hide_request = False
    tf.state = TransformState()
    tf.state.events = True
    tf.state.perspective = perspective
    tf.children = [child]
    tf.offsets = [(0, 0)]
    tf.forward = IDENTITY
    return tf


def test_projective_2d_angled_monitor_focus_and_bounds():
    """Tilted 2D buttons under perspective focus at their projected pixels and report projected focus rects."""

    w, h = 800, 600
    btn_left = object()
    btn_right = object()

    monitor = Render(400, 200)
    monitor.add_focus(btn_left, None, 0, 0, 200, 200)
    monitor.add_focus(btn_right, None, 200, 0, 200, 200)

    # Rotate monitor 35 degrees around Y about its center and place at z=-300 in the scene.
    mon_matrix = (
        Matrix.offset(400, 300, -300)
        * Matrix.rotate(0, 35, 0)
        * Matrix.offset(-200, -100, 0)
    )
    mon_holder = Render(400, 200)
    mon_holder.reverse = mon_matrix
    mon_holder.forward = mon_matrix.inverse()
    mon_holder.blit(monitor, (0, 0))

    root = _make_perspective_root(mon_holder, w, h)
    total = root.reverse * mon_matrix

    lx, ly, _ = _project_point(total, 100, 100)
    rx, ry, _ = _project_point(total, 300, 100)
    ox, oy, _ = _project_point(total, -20, 100)

    assert root.focus_at_point(lx, ly, None) == (btn_left, None, None)
    assert root.focus_at_point(rx, ry, None) == (btn_right, None, None)
    assert root.focus_at_point(ox, oy, None) is None

    u, v = unproject_matrix(total, lx, ly)
    assert u == pytest.approx(100.0, abs=1e-3)
    assert v == pytest.approx(100.0, abs=1e-3)

    focuses = []
    root.take_focuses(0, 0, w, h, IDENTITY, None, focuses)
    assert len(focuses) == 2
    left_focus = next(f for f in focuses if f.widget is btn_left)
    assert left_focus.x <= lx <= left_focus.x + left_focus.w
    assert left_focus.y <= ly <= left_focus.y + left_focus.h


def test_projective_rejects_behind_camera_and_clipped_viewport():
    """Planes behind the camera and buttons scrolled outside a clipped 3D viewport do not focus."""

    w, h = 800, 600
    behind_btn = object()
    behind_r = Render(200, 200)
    behind_r.add_focus(behind_btn, None, 0, 0, 200, 200)

    # Eye sits at z=+1000; placing plane at z=+1500 puts it behind the eye (w < 0).
    behind_holder = Render(200, 200)
    behind_holder.reverse = Matrix.offset(300, 200, 1500)
    behind_holder.forward = behind_holder.reverse.inverse()
    behind_holder.blit(behind_r, (0, 0))

    root_behind = _make_perspective_root(behind_holder, w, h)
    assert root_behind.focus_at_point(400, 300, None) is None

    # Clipped viewport inside an angled monitor.
    visible_btn = object()
    clipped_btn = object()
    inner = Render(200, 400)
    inner.add_focus(visible_btn, None, 0, 0, 200, 100)
    inner.add_focus(clipped_btn, None, 0, 250, 200, 100)

    vp = Render(200, 150)
    vp.xclipping = True
    vp.yclipping = True
    vp.blit(inner, (0, 0))

    mon_matrix = Matrix.offset(400, 300, -200) * Matrix.rotate(0, -25, 0) * Matrix.offset(-100, -75, 0)
    holder = Render(200, 150)
    holder.reverse = mon_matrix
    holder.forward = mon_matrix.inverse()
    holder.blit(vp, (0, 0))

    root_vp = _make_perspective_root(holder, w, h)
    total = root_vp.reverse * mon_matrix

    vx, vy, _ = _project_point(total, 100, 50)
    cx, cy, _ = _project_point(total, 100, 300)
    assert root_vp.focus_at_point(vx, vy, None) == (visible_btn, None, None)
    assert root_vp.focus_at_point(cx, cy, None) is None


def test_mesh3_ray_hit_culling_and_3d_button_focus():
    """Mesh3 ray_hit respects triangle silhouette, backface culling, and 0x0 Button wrappers."""

    w, h = 800, 600
    mesh = _make_mesh3_quad(-50, -50, 50, 50)
    proj = Matrix.perspective(w, h, 100.0, 1000.0, 10000.0)

    front_transform = proj * Matrix.offset(400, 300, -200)
    hit_z = mesh.ray_hit(front_transform, 400.0, 300.0, "ccw")
    assert hit_z is not None
    assert -1.0 <= hit_z <= 1.0
    assert mesh.ray_hit(front_transform, 400, 300, "ccw", hit_z, True) is None
    assert mesh.ray_hit(front_transform, 400, 300, "ccw", hit_z + 0.01, True) == hit_z
    assert mesh.ray_hit(front_transform, 400.0, 300.0, "cw") is None
    assert mesh.ray_hit(front_transform, 100.0, 100.0, "ccw") is None

    # Turn 180 degrees around Y so the quad faces away from the camera.
    back_transform = proj * Matrix.offset(400, 300, -200) * Matrix.rotate(0, 180, 0)
    assert mesh.ray_hit(back_transform, 400.0, 300.0, "ccw") is None
    assert mesh.ray_hit(back_transform, 400.0, 300.0, None) is not None

    # Button wrapping a 0x0 GLTFModel-style Mesh3 Render with focus_mask=True.
    mug_btn = object()
    mesh_render = Render(0, 0)
    mesh_render.mesh = mesh
    mesh_render.reverse = Matrix.offset(400, 300, -200)
    mesh_render.forward = mesh_render.reverse.inverse()

    btn_render = Render(0, 0)
    btn_render.add_property("depth", True)
    btn_render.add_property("cull_face", "ccw")
    btn_render.blit(mesh_render, (0, 0))
    btn_render.add_focus(mug_btn, None, 0, 0, 0, 0, 0, 0, btn_render)

    root = _make_perspective_root(btn_render, w, h, depth=True)
    assert root.focus_at_point(400, 300, None) == (mug_btn, None, None)
    assert root.focus_at_point(50, 50, None) is None

    focuses = []
    root.take_focuses(0, 0, w, h, IDENTITY, None, focuses)
    assert len(focuses) == 1
    assert focuses[0].widget is mug_btn
    assert focuses[0].w > 50 and focuses[0].h > 50


def test_depth_occlusion_and_same_plane_tie_break():
    """Depth-tested 3D scenes pick nearest hits, occlude behind opaque Mesh3, and keep draw order on ties."""

    w, h = 800, 600
    bg_btn = object()
    fg_btn = object()
    nazar_btn = object()

    # Background monitor at z=-400 with Approve button and a Nazar eye drawn AFTER it on the same plane.
    bg_mon = Render(300, 300)
    bg_mon.add_focus(bg_btn, None, 0, 0, 300, 300)
    bg_mon.add_focus(nazar_btn, None, 100, 20, 100, 180)
    bg_holder = Render(300, 300)
    bg_holder.reverse = Matrix.offset(250, 150, -400)
    bg_holder.forward = bg_holder.reverse.inverse()
    bg_holder.blit(bg_mon, (0, 0))

    # Foreground mug button at z=+100, drawn EARLIER in children than bg_holder to prove depth beats draw order.
    fg_mesh = _make_mesh3_quad(-40, -40, 40, 40)
    fg_mesh_r = Render(0, 0)
    fg_mesh_r.mesh = fg_mesh
    fg_mesh_r.reverse = Matrix.offset(400, 300, 100)
    fg_mesh_r.forward = fg_mesh_r.reverse.inverse()
    fg_btn_r = Render(0, 0)
    fg_btn_r.blit(fg_mesh_r, (0, 0))
    fg_btn_r.add_focus(fg_btn, None, 0, 0, 0, 0, 0, 0, fg_btn_r)

    # Non-focusable opaque Mesh3 occluder at (310, 300, z=+50) blocking bg_btn behind it.
    occ_mesh_r = Render(0, 0)
    occ_mesh_r.mesh = _make_mesh3_quad(-25, -25, 25, 25)
    occ_mesh_r.reverse = Matrix.offset(310, 300, 50)
    occ_mesh_r.forward = occ_mesh_r.reverse.inverse()

    # Non-focusable transparent Mesh3 at (490, 300, z=+50) should NOT block bg_btn.
    glass_mesh_r = Render(0, 0)
    glass_mesh_r.mesh = _make_mesh3_quad(-25, -25, 25, 25)
    glass_mesh_r.add_property("transparent", True)
    glass_mesh_r.reverse = Matrix.offset(490, 300, 50)
    glass_mesh_r.forward = glass_mesh_r.reverse.inverse()

    scene = Render(w, h)
    scene.blit(fg_btn_r, (0, 0))
    scene.blit(occ_mesh_r, (0, 0))
    scene.blit(glass_mesh_r, (0, 0))
    scene.blit(bg_holder, (0, 0))

    root = _make_perspective_root(scene, w, h, depth=True)

    # Center (400, 300): foreground mug beats Nazar eye and bg_btn even though drawn first.
    assert root.focus_at_point(400, 300, None) == (fg_btn, None, None)

    # Same-plane Nazar eye above mug's projected box beats bg_btn by draw order.
    bg_total = root.reverse * bg_holder.reverse
    nx, ny, _ = _project_point(bg_total, 150, 40)
    assert root.focus_at_point(nx, ny, None) == (nazar_btn, None, None)

    # Opaque non-focusable occluder blocks bg_btn.
    assert root.focus_at_point(310, 300, None) is None

    # Transparent Mesh3 does not block bg_btn.
    assert root.focus_at_point(490, 300, None) == (bg_btn, None, None)


def test_transform_event_unprojects_into_3d_child_and_cleans_stack():
    """Transform.event passes perspective-unprojected local coords to 3D children and pops its stack on IgnoreEvent."""

    import renpy.display.render as rmod

    received = []

    class Probe:
        def __init__(self, raise_ignore=False):
            self.raise_ignore = raise_ignore

        def event(self, ev, x, y, st):
            received.append((x, y))
            if self.raise_ignore:
                raise renpy.display.core.IgnoreEvent()

    class OffsetContainer:
        def __init__(self, child, xo, yo):
            self.child = child
            self.xo = xo
            self.yo = yo

        def event(self, ev, x, y, st):
            return self.child.event(ev, x - self.xo, y - self.yo, st)

    probe = Probe()
    mon_tf = _event_transform(probe)
    scene_box = OffsetContainer(mon_tf, 0, 0)
    cam_tf = _event_transform(scene_box, perspective=(100.0, 1000.0, 10000.0))
    outer = OffsetContainer(cam_tf, 100, 50)

    # Mirror the Render tree produced by outer(100, 50) -> cam_tf(perspective) -> mon_tf(3D pose) -> probe.
    probe_r = Render(200, 100)
    mon_matrix = Matrix.offset(400, 300, -250) * Matrix.rotate(0, 30, 0) * Matrix.offset(-100, -50, 0)
    mon_r = Render(200, 100)
    mon_r.reverse = mon_matrix
    mon_r.forward = mon_matrix.inverse()
    mon_r.blit(probe_r, (0, 0))
    mon_r.render_of.append(mon_tf)

    cam_r = _make_perspective_root(mon_r, 800, 600)
    cam_r.render_of.append(cam_tf)

    outer_r = Render(1000, 800)
    outer_r.blit(cam_r, (100, 50))

    old_screen_render = rmod.screen_render
    try:
        rmod.screen_render = outer_r
        rmod._projective_map = None
        rmod._projective_map_built = False

        _, m_child = rmod.get_projective_matrices(mon_tf)
        sx, sy, _ = _project_point(m_child, 150.0, 60.0)

        outer.event(object(), sx, sy, 0.0)
        assert len(received) == 1
        assert received[0][0] == pytest.approx(150.0, abs=1e-2)
        assert received[0][1] == pytest.approx(60.0, abs=1e-2)
        assert rmod._projective_event_stack == []

        probe.raise_ignore = True
        with pytest.raises(renpy.display.core.IgnoreEvent):
            outer.event(object(), sx, sy, 0.0)
        assert rmod._projective_event_stack == []
    finally:
        rmod.screen_render = old_screen_render
        rmod._projective_map = None
        rmod._projective_map_built = False
        rmod._projective_event_stack.clear()


def test_free_render_memory_during_perspective_event(mocker):
    """A button callback can free the renderer and still return its interaction result."""
    rmod = renpy.display.render
    mocker.patch.object(renpy.display, "interface", SimpleNamespace(surftree=None))
    mocker.patch.object(rmod, "screen_render", None)

    def clear_and_return(ev, x, y, st):
        rmod.free_memory()
        return "finished"

    camera = _event_transform(SimpleNamespace(event=clear_and_return), perspective=True)
    assert camera.event(object(), 700, 500, 0) == "finished"


def test_nested_interaction_uses_its_own_event_position(mocker):
    """A fresh event dispatched during a perspective button action uses the new interaction's pixel."""
    rmod = renpy.display.render
    nested = _event_transform(SimpleNamespace(event=lambda ev, x, y, st: (x, y)), perspective=True)
    root = _make_perspective_root(Render(800, 600))
    root.render_of.append(nested)
    mocker.patch.object(rmod, "screen_render", root)
    mocker.patch.object(rmod, "_projective_map_built", False)
    mocker.patch.object(rmod, "_projective_map", None)

    def start_nested_interaction(ev, x, y, st):
        return nested.event(object(), 100, 100, st)

    outer = _event_transform(SimpleNamespace(event=start_nested_interaction), perspective=True)
    assert outer.event(object(), 700, 500, 0) == pytest.approx((100, 100))


@pytest.mark.parametrize("modal_last", [True, False])
@pytest.mark.parametrize("peer_z", [-200, 100, 200])
def test_projected_modal_and_sibling_respect_depth_and_draw_order(modal_last, peer_z):
    """Depth decides between a modal and its sibling; coplanar targets follow draw order."""
    button = Render(800, 600)
    button.add_focus("button", None, 0, 0, 800, 600)
    modal = Render(800, 600)
    modal.modal = "window"
    scene = Render(800, 600)
    planes = [(button, peer_z), (modal, 100)]
    for plane, z in planes if modal_last else reversed(planes):
        holder = Render(800, 600)
        holder.reverse = Matrix.offset(0, 0, z)
        holder.blit(plane, (0, 0))
        scene.blit(holder, (0, 0))

    modal_visible = peer_z < 100 or (peer_z == 100 and modal_last)
    peer = ("button", None, None)
    expected = renpy.display.render.Modal if modal_visible else peer
    assert _make_perspective_root(scene).focus_at_point(400, 300, None) == expected

    modal.add_focus("dialog button", None, 0, 0, 800, 600)
    expected = ("dialog button", None, None) if modal_visible else peer
    assert _make_perspective_root(scene).focus_at_point(400, 300, None) == expected


def test_background_only_depth_group_skips_mesh_intersections():
    """Scenery cannot occlude an outside widget or a 3D button that the pointer missed."""
    class DecorativeMesh:
        def ray_hit(self, *args):
            pytest.fail("The background has no mouse target to occlude.")

    background = Render(800, 600)
    background.mesh = DecorativeMesh()
    background.add_focus("keyboard only", Render.NO_MOUSE_FOCUS, 0, 0, 800, 600)
    scene = Render(800, 600)
    scene.add_focus("outside camera", None, 0, 0, 800, 600)
    scene.blit(_make_perspective_root(background), (0, 0))
    assert scene.focus_at_point(400, 300, None) == ("outside camera", None, None)

    background.add_focus("button in corner", None, 0, 0, 50, 50)
    assert scene.focus_at_point(400, 300, None) == ("outside camera", None, None)


def test_keyboard_focus_skips_controls_outside_depth_range():
    """Pointer picking rejects controls in front of the near plane or past the far plane; keyboard focus must too."""

    scene = Render(800, 600)
    buttons = {}
    for name, z in (("near", 950.0), ("visible", 0.0), ("far", -9500.0)):
        buttons[name] = object()
        panel = Render(200, 200)
        panel.add_focus(buttons[name], None, 0, 0, 200, 200)
        holder = Render(200, 200)
        holder.reverse = Matrix.offset(300, 200, z)
        holder.forward = holder.reverse.inverse()
        holder.blit(panel, (0, 0))
        scene.blit(holder, (0, 0))

    focuses = []
    _make_perspective_root(scene).take_focuses(0, 0, 800, 600, IDENTITY, None, focuses)
    assert [f.widget for f in focuses] == [buttons["visible"]]


@pytest.mark.parametrize("alpha_mode, blocked", [(0.0, True), (1.0, False)])
def test_alpha_masked_scenery_does_not_occlude(alpha_mode, blocked):
    """Picking can't sample a cutout's texture, so masked scenery must not block controls seen through its holes."""

    button = object()
    panel = Render(300, 300)
    panel.add_focus(button, None, 0, 0, 300, 300)
    holder = Render(300, 300)
    holder.reverse = Matrix.offset(250, 150, -400)
    holder.forward = holder.reverse.inverse()
    holder.blit(panel, (0, 0))

    cutout = Render(0, 0)
    cutout.mesh = _make_mesh3_quad(-25, -25, 25, 25)
    cutout.add_uniform("u_material_gltf_alphamode", alpha_mode)
    cutout.reverse = Matrix.offset(400, 300, 50)
    cutout.forward = cutout.reverse.inverse()

    scene = Render(800, 600)
    scene.blit(cutout, (0, 0))
    scene.blit(holder, (0, 0))
    hit = _make_perspective_root(scene).focus_at_point(400, 300, None)
    assert hit == (None if blocked else (button, None, None))


@pytest.mark.parametrize("layers", [1, 3])
def test_peeled_scene_reports_one_keyboard_target(layers):
    """Each peel pass redraws the scene's children, but only one copy may contribute focus."""

    button = object()
    child = Render(100, 100)
    child.add_focus(button, None, 0, 0, 100, 100)
    scene = Render(100, 100)
    scene.blit(child, (0, 0))
    peeled = renpy.gl2.gltf.peeled_scene(scene, [], layers, False)
    focuses = []
    peeled.take_focuses(0, 0, 100, 100, IDENTITY, None, focuses)
    assert [f.widget for f in focuses] == [button]
    assert peeled.focus_at_point(50, 50, None)[0] is button


def test_masked_button_mesh_is_scanned_once():
    """A self-masked 3D button already knows its nearest hit, so its occlusion check must not rescan every triangle."""

    class CountingMesh:
        def __init__(self, mesh):
            self.mesh = mesh
            self.bounds = mesh.bounds
            self.calls = 0

        def ray_hit(self, *args):
            self.calls += 1
            return self.mesh.ray_hit(*args)

        def get_points(self):
            return self.mesh.get_points()

    button = object()
    mesh = CountingMesh(_make_mesh3_quad(-40, -40, 40, 40))
    mesh_r = Render(0, 0)
    mesh_r.mesh = mesh
    mesh_r.reverse = Matrix.offset(400, 300, 100)
    mesh_r.forward = mesh_r.reverse.inverse()
    button_r = Render(0, 0)
    button_r.blit(mesh_r, (0, 0))
    button_r.add_focus(button, None, 0, 0, 0, 0, 0, 0, button_r)
    scene = Render(800, 600)
    scene.blit(button_r, (0, 0))

    assert _make_perspective_root(scene).focus_at_point(400, 300, None) == (button, None, None)
    assert mesh.calls == 1
