#!/usr/bin/env python3
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

"""Report glTF texture working sets and constant maps without changing assets (requires Pillow)."""

import argparse
import hashlib
import io
import itertools
import json
import struct
from pathlib import Path
from urllib.parse import unquote, urlsplit


def read_document(data):
    """Reads the JSON of a complete GLB without importing its geometry."""
    if len(data) < 20 or struct.unpack_from("<4sII", data) != (b"glTF", 2, len(data)):
        raise ValueError("Expected a complete glTF 2 GLB")
    size, kind = struct.unpack_from("<II", data, 12)
    if kind != 0x4E4F534A or size % 4 or size > len(data) - 20:
        raise ValueError("Invalid GLB JSON chunk")
    return json.loads(data[20:20 + size])


def texture_path(model, uri):
    """Restricts resource reads to local files below the model directory."""
    parts = urlsplit(uri)
    if parts.scheme or parts.netloc or parts.query or parts.fragment:
        raise ValueError(f"Expected a relative texture filename: {uri!r}")
    relative = Path(unquote(parts.path))
    path = (model.parent / relative).resolve()
    if relative.is_absolute() or not path.is_relative_to(model.parent.resolve()):
        raise ValueError(f"Texture escapes the model directory: {uri!r}")
    return path, relative


def rgba_bytes(width, height):
    """Counts a full RGBA8 mip chain, including non-square and one-pixel textures."""
    total = 0
    while True:
        total += width * height * 4
        if width == height == 1:
            return total
        width, height = max(1, width // 2), max(1, height // 2)


def report(models):
    """Counts shared encoded resources and full mip chains across model transitions."""
    from PIL import Image

    resources = {}
    scenes = []
    paths = [Path(p).resolve(strict=True) for p in models]
    if len({p.name.casefold() for p in paths}) != len(paths):
        raise ValueError("Model basenames must be unique")

    for model in paths:
        payload = model.read_bytes()
        document = read_document(payload)
        keys = set()
        for image in document.get("images", []):
            if "uri" not in image:
                raise ValueError(f"Externalize embedded textures before reporting: {model}")
            path, _relative = texture_path(model, image["uri"])
            data = path.read_bytes()
            digest = hashlib.sha256(data).hexdigest()
            keys.add(digest)
            with Image.open(io.BytesIO(data)) as decoded:
                width, height = decoded.size
                # Metadata-bearing maps stay byte-identical, including their color-space declarations.
                extrema = decoded.getextrema() if decoded.mode in ("RGB", "RGBA") and not decoded.info else ()
                constant = bool(extrema) and all(low == high for low, high in extrema)
            row = resources.setdefault(digest, {"sha256": digest, "files": [], "width": width, "height": height,
                "encoded_bytes": len(data), "rgba_mipmap_bytes": rgba_bytes(width, height), "constant": constant})
            if str(path) not in row["files"]:
                row["files"].append(str(path))
        scenes.append({"model": str(model), "resources": sorted(keys),
            "rgba_mipmap_bytes": sum(resources[k]["rgba_mipmap_bytes"] for k in keys)})

    pairs = []
    for first, second in itertools.combinations(scenes, 2):
        keys = set(first["resources"]) | set(second["resources"])
        pairs.append({"models": [first["model"], second["model"]],
            "rgba_mipmap_bytes": sum(resources[k]["rgba_mipmap_bytes"] for k in keys)})
    pairs.sort(key=lambda pair: pair["rgba_mipmap_bytes"], reverse=True)
    result = {"scenes": scenes, "resources": list(resources.values()), "scene_pairs": pairs,
        "content_deduplicated": True,
        "unique_rgba_mipmap_bytes": sum(r["rgba_mipmap_bytes"] for r in resources.values()),
        "constant_map_savings_bytes": sum(r["rgba_mipmap_bytes"] - 4 for r in resources.values() if r["constant"])}
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("models", nargs="+", type=Path)
    parser.add_argument("--output", type=Path, help="Write the JSON report here, otherwise print it")
    args = parser.parse_args()
    result = json.dumps(report(args.models), indent=2)
    if args.output:
        with args.output.open("x", encoding="utf-8") as stream:
            stream.write(result + "\n")
    else:
        print(result)


if __name__ == "__main__":
    main()
