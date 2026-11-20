"""Tests for renpy.parameter: binding arguments when errors are ignored, as release builds do."""

import pytest

from renpy.parameter import Signature, ValuedParameter as P


@pytest.mark.parametrize(
    "parameters, args, kwargs, expected",
    [
        ([P("a", P.POSITIONAL_OR_KEYWORD), P("c", P.KEYWORD_ONLY, default=0)], (1, 2), {"c": 3}, {"a": 1, "c": 3}),
        ([P("a", P.POSITIONAL_OR_KEYWORD), P("rest", P.VAR_KEYWORD)], (1, 2), {"b": 3}, {"a": 1, "rest": {"b": 3}}),
        ([P("a", P.POSITIONAL_OR_KEYWORD), P("b", P.POSITIONAL_OR_KEYWORD)], (1, 2), {"b": 3}, {"a": 1, "b": 3}),
    ],
)
def test_ignored_errors_keep_keyword_arguments(parameters, args, kwargs, expected):
    """Too many positional arguments, or a collision, consumed the next parameter, dropping its keyword argument."""

    assert Signature(parameters).apply(args, kwargs, ignore_errors=True) == expected
