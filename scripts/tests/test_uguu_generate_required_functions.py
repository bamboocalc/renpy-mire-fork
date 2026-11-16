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

"""Coverage for discovery of mandatory versus optional OpenGL entry points."""

import ast
import importlib.util
from pathlib import Path


def test_required_functions_exclude_format_names_and_optional_queries(tmp_path):
    """glTF signatures and optional timers must not prevent an otherwise supported renderer from starting."""
    path = Path(__file__).parents[1] / "uguu" / "generate_required_functions.py"
    spec = importlib.util.spec_from_file_location("required_functions", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    (tmp_path / "draw.pyx").write_text('glDrawElements(); glDepthMask(); glQueryCounter(); magic = b"glTF"')
    module.process(tmp_path)
    source = (tmp_path / (tmp_path.name + "functions.py")).read_text()
    assert ast.literal_eval(source.split("required_functions = ")[1]) == ["glDepthMask", "glDrawElements"]
