"""Tests for renpy.test.testfilter: parsing filters and matching them against testcases."""

from renpy.test import testfilter
from renpy.test.testsettings import global_testsuite_name


class FakeBlock:
    """A testcase or testsuite with a name, parameter sets, and a parent."""

    def __init__(self, name, parameters=(), parent=None):
        self.name = name
        self.parameters = list(parameters)
        self.parameter_index = 0 if self.parameters else -1
        self.parent = parent

    def get_parent_chain(self):
        chain = []
        current = self.parent
        while current is not None:
            chain.insert(0, current)
            current = current.parent
        return chain

    @property
    def current_full_parameterized_path(self):
        name = self.name
        if self.parameter_index >= 0:
            name += "(" + ", ".join(f"{k}={v!r}" for k, v in self.parameters[self.parameter_index].items()) + ")"
        if self.parent:
            return f"{self.parent.current_full_parameterized_path}.{name}"
        return name


def execution_filter(*filters):
    rv = testfilter.ExecutionFilter.__new__(testfilter.ExecutionFilter)
    rv.include_filters = [testfilter.get_test_filter(i) for i in filters]
    rv.matched_nodes = set()
    rv.parent_nodes = set()
    return rv


def test_tuple_parameter_values_parse():
    """Every closing parenthesis was stripped from the parameters, breaking tuple values."""

    assert testfilter.TestFilterSegment("case(a=(1, 2))").parameters == {"a": (1, 2)}


def test_parameters_without_literal_reprs_can_be_matched():
    """Testcase paths were parsed back from parameter reprs, which raised for objects like characters."""

    root = FakeBlock(global_testsuite_name)
    testcase = FakeBlock("case", [{"who": object()}], parent=root)

    assert execution_filter("case").matches(testcase)


def test_a_later_filter_matches_after_an_earlier_one_lacks_the_parameter():
    """A filter that matched by name but named a missing parameter stopped the other filters being tried."""

    root = FakeBlock(global_testsuite_name)
    testcase = FakeBlock("case", [{"a": 1}], parent=root)

    assert execution_filter("*(b=1)", "case").matches(testcase)
    assert not execution_filter("*(b=1)").matches(testcase)
