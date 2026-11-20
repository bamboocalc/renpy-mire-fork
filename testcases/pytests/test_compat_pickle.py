"""Tests for renpy.compat.pickle: rewriting Python 2 AST nodes."""

import ast

import pytest

from renpy.compat.pickle import ArgumentsWrapper


@pytest.mark.parametrize(
    "vararg, kwarg, args, kwargs, expected",
    [
        (None, None, (1,), {}, (1,)),
        ("rest", "named", (1, 2), {"c": 3}, (1, (2,), {"c": 3})),
    ],
)
def test_python2_arguments_compile(vararg, kwarg, args, kwargs, expected):
    """A Python 2 function without *args or **kwargs got an ast.arg named None, which doesn't compile."""

    arguments = ArgumentsWrapper.__new__(ArgumentsWrapper)
    arguments.__setstate__(
        {"args": [ast.Name("a", ast.Load(), lineno=1, col_offset=0)], "vararg": vararg, "kwarg": kwarg, "defaults": []}
    )

    names = [ast.Name(i, ast.Load()) for i in ("a", vararg, kwarg) if i is not None]
    body = [ast.Return(ast.Tuple(names, ast.Load()))]
    function = ast.FunctionDef("f", arguments, body, [], None, type_params=[], lineno=1, col_offset=0)
    module = ast.fix_missing_locations(ast.Module([function], []))

    namespace = {}
    exec(compile(module, "<python 2>", "exec"), namespace)

    assert namespace["f"](*args, **kwargs) == expected
