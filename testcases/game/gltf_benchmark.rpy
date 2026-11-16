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

    def _gltf_benchmark_frame(scene, st):
        """Keep the current frame through normal render GC, without accumulating hundreds of offscreen targets."""
        import renpy.display.render as renderer
        import renpy.display as display

        rendered = renpy.render(scene, 1280, 720, st, st)
        surface = renpy.render_to_surface(rendered)
        previous = renderer.screen_render
        root = renderer.Render(0, 0)
        root.depends_on(rendered)
        if previous is not None:
            root.depends_on(previous)
        renderer.screen_render = root
        try:
            renderer.mark_sweep()
        finally:
            renderer.screen_render = previous
        display.draw.texture_loader.cleanup()
        return surface

    def _gltf_benchmark_scene(shadows=True, transmission=True, shadow_cache=True, animated=True):
        model = GLTFModel("models/scene-room.gltf", scene=True, shader="renpy.gltf.pbr",
            zoom=65, animation="DoorCycle", animation_loop=True,
            animation_time=None if animated else 0.0, frustum_cull=True)
        camera = Transform(Fixed(Transform(model, xpos=640, ypos=360), xysize=(1280, 720)),
            xysize=(1280, 720), perspective=(1, 700, 10000), gl_depth=True)
        return GLTFScene(camera, light_direction=(0.3, -1, 1), light_color=(0.6, 0.6, 0.6),
            environment=im.Null("#526078"), meters_per_unit=1/65,
            shadows=shadows, transmission=transmission, shadow_cache=shadow_cache), model

    def _gltf_benchmark_run():
        """Measure warmed render plus synchronized readback, never presenting the result as interactive FPS."""
        import hashlib
        import json
        import math
        import os
        import statistics
        import time

        import renpy.display as display
        import renpy.gl2.assimp as assimp

        modes = ("opaque", "shadows", "glass", "static_shadows", "static_glass")
        result = {"renderer": renpy.get_renderer_info(), "size": (1280, 720),
            "warmup_frames": 5, "sample_frames": 30, "profile_frames": 10, "modes": {}}
        for mode, cached_shadows in [(mode, cached) for mode in modes for cached in (False, True)]:
            scene, model = _gltf_benchmark_scene(shadows=mode != "opaque", transmission="glass" in mode,
                shadow_cache=cached_shadows, animated=not mode.startswith("static"))
            started = time.perf_counter()
            with assimp.cache_lock:
                cached = model.cache_key in assimp.cache
            data = model.load()
            imported_ms = (time.perf_counter() - started) * 1000
            for i in range(result["warmup_frames"]):
                _gltf_benchmark_frame(scene, i / 10)
            before = display.draw.texture_loader.get_diagnostics()
            samples, first_hash, last_hash = [], None, None
            for i in range(result["sample_frames"]):
                started = time.perf_counter()
                surface = _gltf_benchmark_frame(scene, (i + result["warmup_frames"]) / 10)
                samples.append((time.perf_counter() - started) * 1000)
                if i in (0, result["sample_frames"] - 1):
                    digest = hashlib.sha256(surface.get_buffer()).hexdigest()
                    if i == 0:
                        first_hash = digest
                    last_hash = digest
            key = mode if cached_shadows else mode + "_uncached"
            result["modes"][key] = {"import_ms": imported_ms, "import_cache_hit": cached,
                "readback_size": surface.get_size(),
                "median_render_readback_ms": statistics.median(samples),
                "p95_render_readback_ms": sorted(samples)[math.ceil(len(samples) * 0.95) - 1],
                "scene_nodes": len(data.asset.order), "mesh_buffers": len(data.mesh_info),
                "mesh_instances": len(data.blit_info), "scene_metadata_bytes": data.scene_memory_size,
                "gpu_before": before, "gpu_after": display.draw.texture_loader.get_diagnostics(),
                "first_frame_hash": first_hash, "last_frame_hash": last_hash}
            # Instrument separately so query overhead does not affect the readback comparison.
            display.draw.set_gltf_profiling()
            try:
                for i in range(result["profile_frames"]):
                    _gltf_benchmark_frame(scene, 10 + i / 10)
                result["modes"][key]["profile"] = display.draw.get_gltf_profile()
            finally:
                display.draw.set_gltf_profiling(False)
            result["modes"][key]["shadow_cache"] = display.draw.texture_loader.shadow_cache.snapshot()
            if mode == "glass" and cached_shadows:
                with open(os.path.join(config.savedir, "gltf-benchmark.png"), "wb") as output:
                    renpy.display.module.save_png(surface, output, 0)
        with open(os.path.join(config.savedir, "gltf-benchmark.json"), "w", encoding="utf-8") as output:
            json.dump(result, output, indent=2, default=str)
        return result

default _gltf_benchmark_results = None

label gltf_scene_benchmark:
    $ _gltf_benchmark_results = _gltf_benchmark_run()
    show expression _gltf_benchmark_scene()[0] as gltf_benchmark_room:
        pos (0, 0)
        anchor (0, 0)
    "Benchmark complete. Reports and a preview image are in the save directory."
    return

testsuite assimp_scene_benchmark:

    testcase run:
        description "The desktop room benchmark exercises animated geometry through opaque, shadowed, and refractive passes."

        run Start("gltf_scene_benchmark")
        advance until "Benchmark complete. Reports and a preview image are in the save directory."
        assert eval _gltf_benchmark_results["modes"]["opaque"]["first_frame_hash"] != _gltf_benchmark_results["modes"]["opaque"]["last_frame_hash"]
        assert eval _gltf_benchmark_results["modes"]["shadows"]["first_frame_hash"] != _gltf_benchmark_results["modes"]["shadows"]["last_frame_hash"]
        assert eval _gltf_benchmark_results["modes"]["glass"]["first_frame_hash"] != _gltf_benchmark_results["modes"]["glass"]["last_frame_hash"]
        assert eval _gltf_benchmark_results["modes"]["opaque"]["last_frame_hash"] != _gltf_benchmark_results["modes"]["glass"]["last_frame_hash"]
        assert eval _gltf_benchmark_results["modes"]["shadows"]["last_frame_hash"] != _gltf_benchmark_results["modes"]["glass"]["last_frame_hash"]
        assert eval all(_gltf_benchmark_results["modes"][mode]["last_frame_hash"] == _gltf_benchmark_results["modes"][mode + "_uncached"]["last_frame_hash"] for mode in ("opaque", "shadows", "glass", "static_shadows", "static_glass"))
        assert eval _gltf_benchmark_results["modes"]["static_shadows"]["profile"]["passes"].get("shadow", {}).get("draws", 0) == 0
        assert eval _gltf_benchmark_results["modes"]["static_shadows_uncached"]["profile"]["passes"]["shadow"]["draws"] > 0
        run MainMenu(confirm=False)
