from pathlib import Path
from typing import ClassVar

from slot_racing.app.runtime import Runtime
from slot_racing.core.config import AppConfig, load_config
from slot_racing.core.plugin import (
    NavigationItem,
    Plugin,
    PluginContext,
    PluginManifest,
    PluginState,
)
from slot_racing.core.storage import Database


def create(config: AppConfig | None = None, **kwargs: object) -> Runtime:
    return Runtime.create(config or AppConfig(), database=Database.in_memory(), **kwargs)  # type: ignore[arg-type]


def nav_ids(runtime: Runtime) -> list[str]:
    return [item.id for item in runtime.contributions.navigation_items()]


def states(runtime: Runtime) -> dict[str, PluginState]:
    return {s.name: s.state for s in runtime.plugins.statuses()}


def test_all_modules_are_discovered_and_hardware_modules_are_opt_in() -> None:
    runtime = create()
    try:
        current = states(runtime)
        assert current["timing_camera"] is PluginState.REGISTERED
        assert current["timing_sensor"] is PluginState.REGISTERED
        assert all(
            state is PluginState.ENABLED
            for name, state in current.items()
            if name not in {"timing_camera", "timing_sensor"}
        )
        assert nav_ids(runtime) == [
            "drivers",
            "vehicles",
            "tracks",
            "races",
            "championships",
            "timing",
            "statistics",
            "track_planner",
        ]
        assert runtime.services.get(Database) is runtime.database
    finally:
        runtime.shutdown()


def test_config_override_enables_or_disables_modules() -> None:
    config = AppConfig(plugin_overrides={"timing_camera": True, "statistics": False})
    runtime = create(config)
    try:
        current = states(runtime)
        assert current["timing_camera"] is PluginState.ENABLED
        assert current["statistics"] is PluginState.REGISTERED
        ids = nav_ids(runtime)
        assert "statistics" not in ids
        assert ids.index("camera_setup") == ids.index("timing") + 1
    finally:
        runtime.shutdown()


def test_disabling_every_module_leaves_a_working_core() -> None:
    runtime = create()
    try:
        for name in runtime.plugins.plugin_names():
            runtime.plugins.disable(name)
        assert nav_ids(runtime) == []
        assert runtime.services.get(Database) is runtime.database
    finally:
        runtime.shutdown()


def test_set_plugin_enabled_is_persisted(tmp_path: Path) -> None:
    config_path = tmp_path / "config.json"
    runtime = create(config_path=config_path)
    try:
        runtime.set_plugin_enabled("statistics", False)
        runtime.set_plugin_enabled("timing_camera", True)
        assert "statistics" not in nav_ids(runtime)
        saved = load_config(config_path)
        assert saved.plugin_overrides == {"statistics": False, "timing_camera": True}
    finally:
        runtime.shutdown()


class _BrokenPlugin(Plugin):
    manifest: ClassVar[PluginManifest] = PluginManifest(name="broken", version="1", title="Broken")

    def activate(self, context: PluginContext) -> None:
        raise RuntimeError("hardware missing")


class _GoodPlugin(Plugin):
    manifest: ClassVar[PluginManifest] = PluginManifest(name="good", version="1", title="Good")

    def activate(self, context: PluginContext) -> None:
        context.add_navigation(NavigationItem(id="good", title_key="nav.good"))


def test_a_failing_plugin_does_not_prevent_startup() -> None:
    runtime = create(plugins=[_BrokenPlugin(), _GoodPlugin()])
    try:
        assert states(runtime) == {"broken": PluginState.FAILED, "good": PluginState.ENABLED}
        assert nav_ids(runtime) == ["good"]
    finally:
        runtime.shutdown()
