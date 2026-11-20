"""Tests for renpy.test.testast: loop control frames, repeat counts, skipping, and hidden python scope."""

import pytest

import renpy
from renpy.test import testast, testexecution

LOC = ("test.rpy", 1)


@pytest.fixture
def frames(mocker):
    """An empty control frame stack, with node changes ignored since no executor is running."""

    mocker.patch.object(testast, "next_node")
    return mocker.patch.object(testexecution, "control_frame_stack", [])


def test_for_pushes_its_frame_once_and_pops_it_when_done(mocker, frames):
    """The body returns to the loop after each pass, which pushed another frame every iteration."""

    mocker.patch.object(testast, "scoped_eval", return_value=[1, 2])
    mocker.patch.object(testast, "scoped_exec")
    loop = testast.For(LOC, "i", "items", testast.Block(LOC, []))

    state = loop.start()
    while state is not None:
        assert frames == [loop]
        state = loop.execute(loop.start(), 0)

    assert frames == []


def test_while_pushes_its_frame_once_and_pops_it_when_done(mocker, frames):
    """While never popped its frame when the condition ended the loop, so a later break went to it."""

    condition = mocker.Mock(spec=testast.Condition)
    condition.ready.side_effect = [True, True, False]
    loop = testast.While(LOC, condition, testast.Block(LOC, [testast.Pass(LOC)]))

    for _ in range(3):
        state = loop.start()
        assert frames == [loop]
        loop.execute(state, 0)

    assert frames == []


class SlowNode(testast.Node):
    """A node that takes three frames per run, and counts its runs."""

    def __init__(self):
        super().__init__(LOC)
        self.runs = 0

    def start(self):
        return 0

    def execute(self, state, t):
        if state == 2:
            self.runs += 1
            return None

        return state + 1


def test_repeat_counts_iterations_rather_than_frames(mocker):
    """Repeat counted every frame, so `pause 1.0 repeat 3` stopped after three frames."""

    mocker.patch.object(testast, "next_node")
    mocker.patch.object(testast, "scoped_eval", side_effect=lambda expr: eval(expr))
    left = SlowNode()
    repeat = testast.Repeat(LOC, left, "3")

    state = repeat.start()
    for frame in range(100):
        if state is None:
            break
        state = repeat.execute(state, frame * 0.001)

    assert left.runs == 3


def test_skip_does_nothing_when_skipping_is_not_allowed(mocker):
    """Skip.start returns None when skipping isn't allowed, which execute ignored."""

    next_node = mocker.patch.object(testast, "next_node")
    mocker.patch.object(renpy.config, "allow_skipping", False)
    mocker.patch.object(renpy.config, "skipping", None)
    skip = testast.Skip(LOC)

    skip.execute(skip.start(), 0)

    assert renpy.config.skipping is None
    next_node.assert_called_once_with(skip.next)


def test_hidden_python_sees_the_test_scope(mocker):
    """Hidden python ran with empty locals, so test parameters and loop variables were undefined."""

    scope = {"parameter": 2}
    mocker.patch.object(testexecution, "scope_stack", [scope])
    store = {"seen": []}
    mocker.patch.dict(renpy.python.store_dicts, {"store": store})

    testexecution.scoped_exec("result = parameter * 2\nseen.append(result)", hide=True)

    assert store["seen"] == [4]
    assert "result" not in store
    assert scope == {"parameter": 2}
