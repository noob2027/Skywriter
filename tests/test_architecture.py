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
    deferred_api_fragments = (
        "PARAM" + "_SET",
        "set_" + "mode_send",
        "MAV_CMD_NAV_" + "RETURN_TO_LAUNCH",
    )
    source = "\n".join(
        path.read_text(encoding="utf-8") for path in sorted(SOURCE_ROOT.rglob("*.py"))
    )
    assert not any(fragment in source for fragment in deferred_api_fragments)


def test_task100_through_103_command_long_emissions_remain_exact_and_confined() -> None:
    fragment = "command_" + "long_send"
    users = {
        path.relative_to(SOURCE_ROOT): path.read_text(encoding="utf-8").count(fragment)
        for path in sorted(SOURCE_ROOT.rglob("*.py"))
        if fragment in path.read_text(encoding="utf-8")
    }

    assert users == {Path("infrastructure/mavlink/connection.py"): 4}
    connection_source = (SOURCE_ROOT / "infrastructure/mavlink/connection.py").read_text(
        encoding="utf-8"
    )
    assert "MAV_CMD_RUN_PREARM_CHECKS = 401" in connection_source
    assert "MAV_CMD_COMPONENT_ARM_DISARM = 400" in connection_source
    assert "MAV_CMD_MISSION_START = 300" in connection_source
    assert "MAV_CMD_DO_PAUSE_CONTINUE = 193" in connection_source
    assert "def send_prearm_checks" in connection_source
    assert "def send_normal_arm" in connection_source
    assert "def send_native_auto_start" in connection_source
    assert "def send_native_pause" in connection_source
    assert "def send_native_resume" in connection_source
    assert "def send_command" not in connection_source
    assert "2989" not in connection_source
    assert "21196" not in connection_source


def test_task103_adds_no_generic_or_later_vehicle_action_surface() -> None:
    source = "\n".join(
        path.read_text(encoding="utf-8") for path in sorted(SOURCE_ROOT.rglob("*.py"))
    )
    prohibited = (
        "def disarm",
        "def set_mode",
        "def start_auto",
        "def land_now",
        "def rtl",
        "def set_parameter",
        "def send_command",
        "def send_setpoint",
    )
    assert not any(fragment in source for fragment in prohibited)


def test_pymavlink_is_confined_to_the_authorized_connection_adapter() -> None:
    fragment = "pyma" + "vlink"
    users = {
        path.relative_to(SOURCE_ROOT)
        for path in sorted(SOURCE_ROOT.rglob("*.py"))
        if fragment in path.read_text(encoding="utf-8")
    }

    assert users == {Path("infrastructure/mavlink/connection.py")}


def test_webengine_is_confined_to_the_authorized_map_host() -> None:
    webengine_fragment = "QtWeb" + "Engine"
    users = {
        path.relative_to(SOURCE_ROOT)
        for path in sorted(SOURCE_ROOT.rglob("*.py"))
        if webengine_fragment in path.read_text(encoding="utf-8")
    }

    assert users == {Path("ui/map/host.py")}
