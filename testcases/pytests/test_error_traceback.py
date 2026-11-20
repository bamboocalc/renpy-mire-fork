"""Tests for renpy.error.TracebackException: exception groups."""

from renpy.error import TracebackException


def test_exception_group_keeps_its_children():
    """The loop variable replaced the group's own traceback, so the children were attached to the last child."""

    try:
        raise ExceptionGroup("several", [ValueError("first"), KeyError("second")])
    except ExceptionGroup as e:
        te = TracebackException(e)

    assert [i.exc_type_str for i in te.exceptions] == ["ValueError", "KeyError"]
    assert all(i.exceptions is None for i in te.exceptions)

    text = te.format()
    assert "first" in text
    assert "second" in text
