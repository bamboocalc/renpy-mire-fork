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

"""Create an independent external-texture GLB staging directory (requires Pillow)."""

import argparse
import hashlib
import io
import json
import struct
import tempfile
from pathlib import Path

from scripts.gltf_texture_report import read_document, texture_path


def resize_texture(image, size, color):
    """Filter colors in linear premultiplied space; data channels remain independent, including alpha."""
    from PIL import Image, ImageMath

    if not color:
        return Image.merge(image.mode, [channel.resize(size, Image.Resampling.LANCZOS) for channel in image.split()])
    channels = image.convert("RGBA").split()
    alpha = channels[3].point([value / 255 for value in range(256)], "F")
    scaled_alpha = alpha.resize(size, Image.Resampling.LANCZOS)
    alpha_values = list(scaled_alpha.get_flattened_data())
    linear = [value / 255 / 12.92 if value / 255 <= 0.04045 else ((value / 255 + 0.055) / 1.055)**2.4
        for value in range(256)]
    output = []
    for channel in channels[:3]:
        decoded = channel.point(linear, "F")
        weighted = ImageMath.lambda_eval(lambda values: values["rgb"] * values["alpha"], rgb=decoded, alpha=alpha)
        filtered = weighted.resize(size, Image.Resampling.LANCZOS)
        encoded = bytearray()
        for value, coverage in zip(filtered.get_flattened_data(), alpha_values):
            value = min(max(value / coverage, 0), 1) if coverage > 0.000001 else 0
            value = value * 12.92 if value <= 0.0031308 else 1.055 * value**(1/2.4) - 0.055
            encoded.append(round(value * 255))
        output.append(Image.frombytes("L", size, bytes(encoded)))
    if image.mode == "RGBA":
        output.append(Image.frombytes("L", size, bytes(round(min(max(value, 0), 1) * 255) for value in alpha_values)))
    return Image.merge(image.mode, output)


def image_uses(document):
    """Find standard texture-info references before choosing a color or data resampling policy."""
    uses = {}

    def visit(value):
        if isinstance(value, list):
            for item in value:
                visit(item)
        elif isinstance(value, dict):
            for name, info in value.items():
                if name.endswith("Texture") and isinstance(info, dict) and "index" in info:
                    texture = info["index"]
                    if type(texture) is not int or not 0 <= texture < len(document.get("textures", [])):
                        raise ValueError("Invalid material texture index.")
                    source = document["textures"][texture].get("source")
                    if source is not None:
                        if type(source) is not int or not 0 <= source < len(document.get("images", [])):
                            raise ValueError("Invalid texture image index.")
                        color = name in ("baseColorTexture", "emissiveTexture", "specularColorTexture", "sheenColorTexture")
                        uses.setdefault(source, set()).add(color)
                else:
                    visit(info)

    visit(document.get("materials", []))
    return uses


def stage(model, output, max_size=None):
    """Publish a new directory atomically; source assets and existing output directories are never overwritten."""
    from PIL import Image, UnidentifiedImageError

    model, output = Path(model).resolve(strict=True), Path(output).resolve()
    if output.exists() or output.is_relative_to(model.parent):
        raise ValueError("Output must be a new directory outside the source asset directory.")
    if max_size is not None and (type(max_size) is not int or max_size < 1):
        raise ValueError("max_size must be a positive integer.")
    original = model.read_bytes()
    document = read_document(original)
    tail = original[20 + struct.unpack_from("<I", original, 12)[0]:]
    uses, records, resources = image_uses(document), [], {}
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".gltf-stage-", dir=output.parent) as temporary:
        target = Path(temporary)
        (target / "resources").mkdir()

        def resource(payload, suffix):
            digest = hashlib.sha256(payload).hexdigest()
            name = f"resources/{digest}{suffix}"
            (target / name).write_bytes(payload)
            resources[name] = len(payload)
            return name

        for index, entry in enumerate(document.get("images", [])):
            if "uri" not in entry:
                raise ValueError("Externalize embedded images before staging.")
            path, _relative = texture_path(model, entry["uri"])
            payload, suffix = path.read_bytes(), path.suffix.lower()
            before, after = None, None
            try:
                image = Image.open(io.BytesIO(payload))
            except UnidentifiedImageError:
                image = None
            if image is not None:
                with image:
                    before = after = image.size
                    if image.format == "PNG" and image.mode in ("RGB", "RGBA") and not image.info:
                        if all(low == high for low, high in image.getextrema()):
                            image = Image.new(image.mode, (1, 1), image.getpixel((0, 0)))
                        elif max_size is not None and max(image.size) > max_size:
                            if len(uses.get(index, ())) != 1:
                                raise ValueError("Resizing needs an unambiguous color/data use; split shared or unused images first.")
                            factor = max_size / max(image.size)
                            image = resize_texture(image,
                                tuple(max(1, round(value * factor)) for value in image.size), next(iter(uses[index])))
                        after = image.size
                        if after != before:
                            encoded = io.BytesIO()
                            image.save(encoded, format="PNG")
                            payload, suffix = encoded.getvalue(), ".png"
                    elif max_size is not None and max(image.size) > max_size:
                        raise ValueError("Resizing supports metadata-free RGB/RGBA PNGs; normalize other images first.")
            entry["uri"] = resource(payload, suffix)
            records.append({"source": str(path), "before": before, "after": after, "uri": entry["uri"]})
        for buffer in document.get("buffers", []):
            if "uri" in buffer:
                path, _relative = texture_path(model, buffer["uri"])
                buffer["uri"] = resource(path.read_bytes(), path.suffix.lower())
        encoded = json.dumps(document, separators=(",", ":")).encode("utf-8")
        encoded += b" " * (-len(encoded) % 4)
        payload = struct.pack("<4sIIII", b"glTF", 2, 20 + len(encoded) + len(tail), len(encoded), 0x4E4F534A)
        (target / model.name).write_bytes(payload + encoded + tail)
        report = {"images": records, "unique_resources": len(resources), "resource_bytes": sum(resources.values())}
        (target / "texture-stage.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        target.rename(output)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("model", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--max-size", type=int, help="Optional lossy size cap; default only shrinks constant maps.")
    args = parser.parse_args()
    print(json.dumps(stage(args.model, args.output, args.max_size), indent=2))


if __name__ == "__main__":
    main()
