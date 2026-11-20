"""Tests for renpy.test.testparser: how conditions combine."""

import pytest

from renpy.lexer import GroupedLine, Lexer
from renpy.test import testast, testparser


def parse(text):
    lexer = Lexer([GroupedLine("test.rpy", 1, 0, text, [])])
    lexer.advance()
    return testparser.parse_condition(lexer, ("test.rpy", 1))


def shape(condition):
    """Writes a condition as nested tuples, with label conditions as their names."""

    if isinstance(condition, testast.Label):
        return condition.name
    if isinstance(condition, testast.Not):
        return ("not", shape(condition.condition))
    if isinstance(condition, testast.And):
        return ("and", shape(condition.left), shape(condition.right))
    if isinstance(condition, testast.Or):
        return ("or", shape(condition.left), shape(condition.right))
    raise TypeError(condition)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("not label a and label b", ("and", ("not", "a"), "b")),
        ("label a and label b or label c", ("or", ("and", "a", "b"), "c")),
        ("label a or label b and label c", ("or", "a", ("and", "b", "c"))),
        ("label a or label b or label c", ("or", ("or", "a", "b"), "c")),
        ("not (label a or label b)", ("not", ("or", "a", "b"))),
    ],
)
def test_conditions_bind_like_python(text, expected):
    """`not` took everything after it, and `and` and `or` grouped to the right with equal precedence."""

    assert shape(parse(text)) == expected
