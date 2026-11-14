import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

import renpy
from renpy.display import im
from renpy.display.render import Render
from renpy.gl2.gl2model import GL2Model
from renpy.gl2.gl2texture import Texture, TextureLoader

pytestmark = pytest.mark.usefixtures("surface_deallocation")


@pytest.fixture(autouse=True)
def draw(mocker):
    """
    A mock draw object that reports textures as not ready and returns the surface passed to load_texture
    unchanged, so the cache can be exercised without a graphics device. It is patched into every test.
    """

    draw = mocker.Mock()
    draw.ready_one_texture.return_value = False
    draw.load_texture.side_effect = lambda surface, **kwargs: surface
    mocker.patch.object(renpy.display, "draw", draw)
    return draw


@pytest.fixture
def cache(mocker):
    """
    An image cache built with renpy.emscripten set, which skips starting the preload thread. The flag is
    restored right after construction. Teardown clears the cache so no surfaces outlive the test.
    """

    emscripten = mocker.patch.object(renpy, "emscripten", True)
    cache = im.Cache()
    mocker.stop(emscripten)

    yield cache

    cache.cache.clear()


def assert_uncached(cache):
    """Checks that the cache holds no entries and reports zero total size."""

    assert cache.cache == {}
    assert cache.get_total_size() == 0


def test_diagnostics_distinguish_reuse_decode_and_eviction(mocker, cache):
    """A residency snapshot explains a reused texture and releases its accounting after eviction."""

    mocker.patch.object(renpy.config, "debug_image_cache", True)
    cache.cache_limit = 1024
    image = im.Null()
    cache.get(image, texture=True)
    cache.get(image, texture=True)

    snapshot = cache.get_diagnostics()
    assert snapshot["hits"] == 1
    assert snapshot["decodes"] == 1
    assert snapshot["cache_bytes"] == cache.get_total_size() * 4
    assert snapshot["current_bytes"] == snapshot["cache_bytes"]
    assert snapshot["entries"][0]["texture_created"]

    cache.kill(cache.cache[image])
    after = cache.get_diagnostics()
    assert after["evictions"] == 1
    assert after["cache_bytes"] == 0
    assert after["entries"] == []
    assert snapshot["entries"]


@pytest.mark.parametrize("cache_surfaces", [False, True])
def test_uncached_surface_reloads(mocker, cache, draw, cache_surfaces):
    """
    An image created with cache=False is reloaded on every get, returning a new surface each time, and
    leaves nothing in the cache. This holds whether or not config.cache_surfaces is set, since
    cache=False takes precedence.
    """

    mocker.patch.object(renpy.config, "cache_surfaces", cache_surfaces)
    image = im.Null(cache=False)
    first = cache.get(image)
    second = cache.get(image)
    assert isinstance(first, renpy.pygame.Surface)
    assert isinstance(second, renpy.pygame.Surface)
    assert first is not second
    assert_uncached(cache)
    draw.load_texture.assert_not_called()


def test_uncached_texture_and_image_render(mocker, cache, draw):
    """
    Requesting an uncached image as a texture loads it once and returns that texture. Rendering the same
    uncached image loads a second texture and returns a Render of the image's size, without retaining
    anything in the cache.
    """

    image = im.Null(cache=False)
    texture = cache.get(image, texture=True)
    draw.load_texture.assert_called_once()
    assert texture is draw.load_texture.call_args.args[0]

    cache_patch = mocker.patch.object(im, "cache", cache)
    render = image.render(100, 100, 0, 0)
    mocker.stop(cache_patch)

    assert isinstance(render, Render)
    assert render.get_size() == (1, 1)
    assert draw.load_texture.call_count == 2
    assert_uncached(cache)


def test_uncached_render_matches_cached_bounds_and_properties(mocker, cache, draw):
    """
    With texture bounds optimization on, an uncached render and a cached render of the same image produce
    the same size, child offset, child surface size, and pixel_perfect property. Only one entry lands in
    the cache, from the cached image. Texture loads from both use the mipmap property. The patches are
    removed after the renders are built.
    """

    surface = renpy.pygame.Surface((12, 8))
    surface.fill((0, 0, 0, 0))
    surface.fill((255, 255, 255, 255), (4, 2, 4, 4))
    patches = [
        mocker.patch.object(renpy.config, "optimize_texture_bounds", True),
        mocker.patch.object(renpy.config, "expand_texture_bounds", 0),
        mocker.patch.object(im.Null, "load", return_value=surface),
        mocker.patch.object(im.Null, "pixel_perfect", True),
    ]
    renders = [
        cache.get(im.Null(cache=use_cache, mipmap=True, oversample=2), render=True) for use_cache in (False, True)
    ]
    for patched in patches:
        mocker.stop(patched)

    assert [r.get_size() for r in renders] == [(6, 4), (6, 4)]
    assert [r.children[0][1:3] for r in renders] == [(2, 1), (2, 1)]
    assert [r.children[0][0].get_size() for r in renders] == [(4, 4), (4, 4)]
    assert [r.properties for r in renders] == [{"pixel_perfect": True}] * 2
    assert len(cache.cache) == 1
    for call in draw.load_texture.call_args_list:
        assert call.kwargs == {"properties": {"mipmap": True}}


def test_uncached_prediction_does_not_retain_texture(cache, draw):
    """
    Predicting an uncached image loads its texture each time but returns None and retains nothing, and
    it never consults ready_one_texture, which is only used for cached textures.
    """

    image = im.Null(cache=False)
    for _ in range(2):
        assert cache.get(image, predict=True, render=True) is None
        assert_uncached(cache)
    assert draw.load_texture.call_count == 2
    draw.ready_one_texture.assert_not_called()


def test_uncached_images_do_not_log_cache_changes(mocker, cache):
    """
    Uncached images, whether fetched, predicted, or rendered, don't write to the image cache debug log,
    even with config.debug_image_cache enabled, because they never enter the cache.
    """

    mocker.patch.object(renpy.config, "debug_image_cache", True)
    mocker.patch.object(renpy.config, "cache_surfaces", False)
    log = mocker.patch.object(renpy.display, "ic_log")

    image = im.Null(cache=False)
    cache.get(image)
    cache.get(image, predict=True, render=True)
    cache.get(image, render=True)
    log.write.assert_not_called()
    assert_uncached(cache)


def test_cached_prediction_is_reused_for_rendering(cache, draw):
    """
    Predicting a cached image loads its texture once, and later render requests reuse that texture and
    its entry instead of loading again. The cache holds one entry with a nonzero total size.
    """

    image = im.Null()
    assert cache.get(image, predict=True, render=True) is None
    first = cache.get(image, render=True)
    second = cache.get(image, render=True)
    assert isinstance(first, Render)
    assert isinstance(second, Render)
    assert first.children[0][0] is second.children[0][0]
    draw.load_texture.assert_called_once()
    assert len(cache.cache) == 1
    assert cache.get_total_size() > 0


@pytest.mark.parametrize(
    ("premultiply", "mipmap", "properties"),
    [
        (True, None, {}),
        (False, None, {"premultiplied": True}),
        (True, False, {"mipmap": False}),
        (False, True, {"premultiplied": True, "mipmap": True}),
    ],
    ids=["default", "no-premultiply", "mipmap-false", "both"],
)
@pytest.mark.parametrize("raw", [False, True], ids=["image", "raw-texture"])
def test_texture_load_properties(cache, draw, raw, premultiply, mipmap, properties):
    """
    Loading an image as a texture passes premultiply and mipmap to load_texture as texture properties.

    premultiply=False is sent as premultiplied=True, which tells the loader the surface needs no
    premultiplication. mipmap=False must be sent explicitly rather than dropped, since leaving it out
    would fall back to config.mipmap. Runs on both a plain image and a RawTexture wrapping one.
    """

    if raw:
        image = im.UnoptimizedTexture(im.Null(), premultiply=premultiply, mipmap=mipmap)
    else:
        image = im.Null(premultiply=premultiply, mipmap=mipmap)

    cache.get(image, texture=True)
    assert draw.load_texture.call_args.kwargs == {"properties": properties}


def test_premultiply_setting_gets_separate_cache_entry(cache, draw):
    """
    RawTextures of the same image that differ only in premultiply are cached as separate entries with
    separate texture loads, so one never reuses a texture loaded with the other's setting.
    """

    premultiplied = im.UnoptimizedTexture(im.Null())
    raw = im.UnoptimizedTexture(im.Null(), premultiply=False)
    assert premultiplied.identity != raw.identity

    cache.get(premultiplied, texture=True)
    cache.get(raw, texture=True)
    assert len(cache.cache) == 2
    assert [call.kwargs for call in draw.load_texture.call_args_list] == [
        {"properties": {}},
        {"properties": {"premultiplied": True}},
    ]


def slow_loader(mocker, image):
    """
    Replaces image.load with one that signals `started`, then waits for `release` before decoding, and records
    each call's thread.
    """

    started, release = threading.Event(), threading.Event()
    original = image.load
    calls = []

    def load():
        calls.append(threading.current_thread().name)
        started.set()
        assert release.wait(5)
        return original()

    mocker.patch.object(image, "load", side_effect=load)
    return started, release, calls


def test_concurrent_requests_share_one_decode(mocker, cache, draw):
    """A thread that needs an image another thread is decoding waits for that decode instead of repeating it."""

    image = im.Null()
    started, release, calls = slow_loader(mocker, image)

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(cache.get, image, texture=True)
        assert started.wait(5)
        second = pool.submit(cache.get, image, texture=True)
        time.sleep(0.1)
        assert not second.done()
        release.set()
        assert first.result(5) is second.result(5)

    assert len(calls) == 1
    assert cache.loading == {}


def test_failed_decode_releases_waiters(mocker, cache, draw):
    """When the decoding thread fails, a waiting thread decodes the image itself and reports its own error."""

    image = im.Null()
    started, release, calls = slow_loader(mocker, image)

    def failing():
        calls.append(threading.current_thread().name)
        started.set()
        assert release.wait(5)
        raise ValueError("bad image")

    image.load.side_effect = failing

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(cache.get, image, texture=True)
        assert started.wait(5)
        second = pool.submit(cache.get, image, texture=True)
        time.sleep(0.1)
        release.set()

        for future in (first, second):
            with pytest.raises(ValueError, match="bad image"):
                future.result(5)

    assert len(calls) == 2
    assert cache.loading == {}


def test_get_textures_decodes_on_the_pool_and_uploads(mocker, cache, draw):
    """Uncached images are decoded on the decode pool and uploaded on the calling thread; others are skipped."""

    pool = ThreadPoolExecutor(max_workers=2)
    mocker.patch.object(cache, "get_decode_pool", return_value=pool)
    images = [im.Null(mipmap=True), im.Null(mipmap=False)]
    textures = []

    def load_texture(surface, **kwargs):
        texture = RecordingTexture()
        textures.append(texture)
        return texture

    draw.load_texture.side_effect = load_texture

    try:
        cache.get_textures(images + images + [Render(1, 1)])
    finally:
        pool.shutdown()

    assert all(cache.cache[i].texture is not None for i in images)
    assert len(textures) == 2
    assert all(t.upload_threads == [threading.current_thread()] for t in textures)
    draw.ready_one_texture.assert_not_called()


class RecordingTexture(GL2Model):
    """Records upload requests without needing a GPU or a live graphics context."""

    def __init__(self):
        super().__init__((1, 1), None, ())
        self.upload_threads = []

    def load(self):
        self.upload_threads.append(threading.current_thread())


def test_demand_does_not_drain_unrelated_predicted_uploads(cache, draw):
    """An immediately needed image must not wait for the next scene's queued GPU uploads."""

    requested = RecordingTexture()
    draw.load_texture.side_effect = lambda *args, **kwargs: requested
    draw.ready_one_texture.side_effect = AssertionError("Unrelated prediction queue was drained")

    assert cache.get(im.Null(), texture=True) is requested
    assert requested.upload_threads == [threading.current_thread()]
    draw.ready_one_texture.assert_not_called()


def test_parallel_texture_loading_preserves_control_flow(mocker, cache):
    """A control-flow exception from a worker must reach the caller instead of being treated as a failed decode."""

    mocker.patch.object(cache, "preload_texture", side_effect=renpy.game.JumpException("target"))
    with ThreadPoolExecutor(max_workers=2) as pool:
        mocker.patch.object(cache, "get_decode_pool", return_value=pool)
        with pytest.raises(renpy.game.JumpException, match="target"):
            cache.get_textures([im.Null(mipmap=True), im.Null(mipmap=False)])


def test_demand_finishes_each_tile_of_a_large_image(cache, draw):
    """A texture split by the GPU size limit finishes all its tiles on the requesting thread."""

    first, second = RecordingTexture(), RecordingTexture()
    tiled = Render(2, 1)
    tiled.blit(first, (0, 0))
    tiled.blit(second, (1, 0))
    draw.load_texture.side_effect = lambda *args, **kwargs: tiled

    assert cache.get(im.Null(), texture=True) is tiled
    assert first.upload_threads == second.upload_threads == [threading.current_thread()]
    draw.ready_one_texture.assert_not_called()


def test_prediction_waits_for_the_last_decode_batch_and_new_requests(mocker, cache):
    """An empty input queue is not completion while its last batch or a newer prediction request is pending."""

    image = im.Null()
    started, release, _calls = slow_loader(mocker, image)
    mocker.patch("renpy.gl2.assimp.preload")
    cache.cache_limit = 1024
    cache.decode_pool_workers = 1
    cache.preload_image(image)

    with ThreadPoolExecutor(max_workers=2) as pool:
        mocker.patch.object(cache, "get_decode_pool", return_value=pool)
        future = pool.submit(lambda: list(cache.preload_thread_pass()))
        try:
            assert started.wait(5)
            assert not cache.preloads
            assert not cache.done()
            cache.start_prediction()
        finally:
            release.set()
        future.result(5)

    assert not cache.done()
    list(cache.preload_thread_pass())
    assert cache.done()


def test_prediction_waits_for_pending_gpu_uploads(cache, draw):
    """A queued texture is CPU-ready but must not end prediction until its GPU work is finished or discarded."""

    loader = TextureLoader(None)
    draw.texture_loader = loader
    texture = Texture((1, 1), loader)
    surface = renpy.pygame.Surface((1, 1))
    texture.from_surface(surface, {"mipmap": False})

    assert not cache.done()
    del texture
    assert cache.done()
