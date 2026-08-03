import pytest

from strategy_lab.registry import DuplicateRegistration, UnknownPlugin, get, list_plugins, register


def test_register_and_get_roundtrip():
    @register("widget", "example_registry_widget")
    class Example:
        pass

    assert get("widget", "example_registry_widget") is Example
    assert "example_registry_widget" in list_plugins("widget")


def test_get_unknown_plugin_raises():
    with pytest.raises(UnknownPlugin):
        get("widget", "does_not_exist")


def test_duplicate_registration_raises():
    @register("widget", "dup_widget")
    class First:
        pass

    with pytest.raises(DuplicateRegistration):

        @register("widget", "dup_widget")
        class Second:
            pass
