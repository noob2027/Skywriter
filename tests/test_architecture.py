"""Architecture and dependency-direction tests for the foundation."""

import ast
from pathlib import Path

SOURCE_ROOT = Path(__file__).parents[1] / "src" / "skywriter"


def imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            imports.add(node.module)
    return imports


def test_domain_and_application_dependencies_point_inward() -> None:
    forbidden_by_layer = {
        "domain": ("skywriter.application", "skywriter.infrastructure", "skywriter.ui"),
        "application": ("skywriter.infrastructure", "skywriter.ui"),
    }

    for layer, forbidden in forbidden_by_layer.items():
        for path in (SOURCE_ROOT / layer).rglob("*.py"):
            imports = imported_modules(path)
            assert not any(
                module == prefix or module.startswith(f"{prefix}.")
                for module in imports
                for prefix in forbidden
            ), f"{path} crosses its dependency boundary: {sorted(imports)}"


def test_inner_layers_do_not_import_optional_or_ui_frameworks() -> None:
    forbidden_roots = ("PySide6", "pyma" + "vlink", "ser" + "ial")

    for layer in ("domain", "application"):
        for path in (SOURCE_ROOT / layer).rglob("*.py"):
            imports = imported_modules(path)
            assert not any(
                module == root or module.startswith(f"{root}.")
                for module in imports
                for root in forbidden_roots
            ), f"{path} imports a forbidden framework: {sorted(imports)}"


def test_compatibility_envelopes_are_pure_and_transport_independent() -> None:
    forbidden = (
        "skywriter.application",
        "skywriter.infrastructure",
        "skywriter.ui",
        "PySide6",
        "pymavlink",
        "serial",
    )

    for path in (SOURCE_ROOT / "compatibility").rglob("*.py"):
        imports = imported_modules(path)
        assert not any(
            module == prefix or module.startswith(f"{prefix}.")
            for module in imports
            for prefix in forbidden
        ), f"{path} crosses the pure compatibility boundary: {sorted(imports)}"


def test_source_has_no_deferred_vehicle_or_parameter_apis() -> None:
    deferred_api_fragments = ("pyma" + "vlink", "PARAM" + "_SET")
    source = "\n".join(
        path.read_text(encoding="utf-8") for path in sorted(SOURCE_ROOT.rglob("*.py"))
    )
    assert not any(fragment in source for fragment in deferred_api_fragments)


def test_webengine_is_confined_to_the_authorized_map_host() -> None:
    webengine_fragment = "QtWeb" + "Engine"
    users = {
        path.relative_to(SOURCE_ROOT)
        for path in sorted(SOURCE_ROOT.rglob("*.py"))
        if webengine_fragment in path.read_text(encoding="utf-8")
    }

    assert users == {Path("ui/map/host.py")}
