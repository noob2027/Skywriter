"""Package import smoke tests."""

import importlib
import pkgutil

import skywriter


def test_every_skywriter_package_imports() -> None:
    module_names = [
        module_info.name
        for module_info in pkgutil.walk_packages(skywriter.__path__, prefix="skywriter.")
    ]

    assert module_names
    for module_name in module_names:
        importlib.import_module(module_name)
