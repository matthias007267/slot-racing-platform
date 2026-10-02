from collections.abc import Callable
from typing import ClassVar

import pytest

from slot_racing.core.clock import ManualClock
from slot_racing.core.config import AppConfig
from slot_racing.core.domain import RaceId
from slot_racing.core.events import Event, EventBus, PluginDisabled, PluginEnabled, RaceStarting
from slot_racing.core.i18n import Translator
from slot_racing.core.plugin import (
    ContributionRegistry,
    NavigationItem,
    Plugin,
    PluginActivationError,
    PluginContext,
    PluginDependencyError,
    PluginError,
    PluginManager,
    PluginManifest,
    PluginState,
    ServiceNotFoundError,
    ServiceRegistry,
    discover_plugins,
)
from slot_racing.core.timing import TimingSource


class _Greeter:
    pass


def make_plugin(
    name: str,
    *,
    requires: tuple[str, ...] = (),
    optional: tuple[str, ...] = (),
    on_activate: Callable[[PluginContext], None] | None = None,
) -> Plugin:
    class _Plugin(Plugin):
        manifest: ClassVar[PluginManifest] = PluginManifest(
            name=name, version="1.0", title=name.title(), requires=requires, optional=optional
        )
        translations: ClassVar[dict[str, dict[str, str]]] = {
            "de": {f"plugin.{name}.title": "Titel"}
        }

        def activate(self, context: PluginContext) -> None:
            context.add_navigation(NavigationItem(id=name, title_key=f"nav.{name}"))
            context.register_service(_Greeter, _Greeter())
            if on_activate is not None:
                on_activate(context)

    return _Plugin()


class Env:
    def __init__(self) -> None:
        self.bus = EventBus()
        self.services = ServiceRegistry()
        self.contributions = ContributionRegistry()
        self.manager = PluginManager(
            bus=self.bus,
            services=self.services,
            contributions=self.contributions,
            translator=Translator(),
            clock=ManualClock(),
            config=AppConfig(),
        )
        self.events: list[Event] = []
        self.bus.subscribe(Event, self.events.append)

    def nav_ids(self) -> list[str]:
        return [item.id for item in self.contributions.navigation_items()]


@pytest.fixture
def env() -> Env:
    return Env()


def test_manifest_validation() -> None:
    with pytest.raises(ValueError, match="invalid plugin name"):
        PluginManifest(name="Bad-Name", version="1", title="x")
    with pytest.raises(ValueError, match="itself"):
        PluginManifest(name="a", version="1", title="x", requires=("a",))


def test_enable_registers_contributions_and_publishes_event(env: Env) -> None:
    env.manager.register(make_plugin("alpha"))
    env.manager.enable("alpha")
    assert env.manager.is_enabled("alpha")
    assert env.nav_ids() == ["alpha"]
    assert env.services.find(_Greeter) is not None
    assert [type(e) for e in env.events] == [PluginEnabled]


def test_disable_removes_everything_the_plugin_registered(env: Env) -> None:
    seen: list[Event] = []

    def subscribe(context: PluginContext) -> None:
        context.events.subscribe(RaceStarting, seen.append)

    env.manager.register(make_plugin("alpha", on_activate=subscribe))
    env.manager.enable("alpha")
    env.manager.disable("alpha")

    assert env.nav_ids() == []
    assert env.services.find(_Greeter) is None
    env.bus.publish(RaceStarting(timestamp_ns=1, race_id=RaceId(1)))
    assert seen == []
    assert PluginDisabled in [type(e) for e in env.events]
    assert env.manager.statuses()[0].state is PluginState.DISABLED


def test_disabled_plugin_can_be_enabled_again(env: Env) -> None:
    env.manager.register(make_plugin("alpha"))
    env.manager.enable("alpha")
    env.manager.disable("alpha")
    env.manager.enable("alpha")
    assert env.nav_ids() == ["alpha"]


def test_enable_requires_dependencies_to_be_enabled(env: Env) -> None:
    env.manager.register(make_plugin("base"))
    env.manager.register(make_plugin("child", requires=("base",)))
    with pytest.raises(PluginDependencyError):
        env.manager.enable("child")
    assert not env.manager.is_enabled("child")


def test_disabling_a_plugin_disables_its_dependents(env: Env) -> None:
    env.manager.register(make_plugin("base"))
    env.manager.register(make_plugin("child", requires=("base",)))
    env.manager.register(make_plugin("other"))
    env.manager.enable_many(["base", "child", "other"])
    env.manager.disable("base")
    assert not env.manager.is_enabled("child")
    assert env.manager.is_enabled("other")
    assert env.nav_ids() == ["other"]


def test_enable_many_orders_by_dependencies(env: Env) -> None:
    order: list[str] = []

    def recorder(name: str) -> Callable[[PluginContext], None]:
        return lambda _context: order.append(name)

    for name, requires in [("c", ("b",)), ("b", ("a",)), ("a", ())]:
        env.manager.register(make_plugin(name, requires=requires, on_activate=recorder(name)))
    env.manager.enable_many(["c", "b", "a"])
    assert order == ["a", "b", "c"]


def test_optional_dependency_only_affects_order(env: Env) -> None:
    order: list[str] = []
    env.manager.register(
        make_plugin("late", optional=("early",), on_activate=lambda _c: order.append("late"))
    )
    env.manager.register(make_plugin("early", on_activate=lambda _c: order.append("early")))
    env.manager.enable_many(["late", "early"])
    assert order == ["early", "late"]

    env.manager.disable("early")
    assert env.manager.is_enabled("late")


def test_optional_dependency_may_be_absent(env: Env) -> None:
    env.manager.register(make_plugin("late", optional=("missing",)))
    env.manager.enable_many(["late"])
    assert env.manager.is_enabled("late")


def test_failing_activation_is_isolated_and_rolled_back(env: Env) -> None:
    def explode(_context: PluginContext) -> None:
        raise RuntimeError("camera not available")

    env.manager.register(make_plugin("broken", on_activate=explode))
    env.manager.register(make_plugin("healthy"))
    env.manager.register(make_plugin("dependent", requires=("broken",)))
    env.manager.enable_many(["broken", "healthy", "dependent"])

    states = {s.name: s for s in env.manager.statuses()}
    assert states["broken"].state is PluginState.FAILED
    assert "camera not available" in (states["broken"].error or "")
    assert states["dependent"].state is PluginState.FAILED
    assert states["healthy"].state is PluginState.ENABLED
    assert env.nav_ids() == ["healthy"]
    assert len(env.services.find_all(_Greeter)) == 1


def test_explicit_enable_of_failing_plugin_raises(env: Env) -> None:
    def explode(_context: PluginContext) -> None:
        raise RuntimeError("boom")

    env.manager.register(make_plugin("broken", on_activate=explode))
    with pytest.raises(PluginActivationError):
        env.manager.enable("broken")


def test_failing_deactivate_does_not_block_disable(env: Env) -> None:
    class Sloppy(Plugin):
        manifest: ClassVar[PluginManifest] = PluginManifest(name="sloppy", version="1", title="S")

        def deactivate(self) -> None:
            raise RuntimeError("cannot clean up")

    env.manager.register(Sloppy())
    env.manager.enable("sloppy")
    env.manager.disable("sloppy")
    assert env.manager.statuses()[0].state is PluginState.DISABLED


def test_dependency_cycle_marks_plugins_failed(env: Env) -> None:
    env.manager.register(make_plugin("a", requires=("b",)))
    env.manager.register(make_plugin("b", requires=("a",)))
    env.manager.register(make_plugin("free"))
    env.manager.enable_many(["a", "b", "free"])
    states = {s.name: s.state for s in env.manager.statuses()}
    assert states == {"a": PluginState.FAILED, "b": PluginState.FAILED, "free": PluginState.ENABLED}


def test_duplicate_and_unknown_plugins(env: Env) -> None:
    env.manager.register(make_plugin("a"))
    with pytest.raises(PluginError, match="already registered"):
        env.manager.register(make_plugin("a"))
    with pytest.raises(PluginError, match="unknown"):
        env.manager.enable("nope")


def test_shutdown_disables_everything(env: Env) -> None:
    env.manager.register(make_plugin("base"))
    env.manager.register(make_plugin("child", requires=("base",)))
    env.manager.enable_many(["base", "child"])
    env.manager.shutdown()
    assert env.nav_ids() == []


def test_load_failures_are_reported(env: Env) -> None:
    env.manager.record_load_failure("ghost", "ImportError: no module")
    status = env.manager.statuses()[0]
    assert (status.name, status.state) == ("ghost", PluginState.FAILED)


def test_titles_come_from_translations(env: Env) -> None:
    env.manager.register(make_plugin("alpha"))
    assert env.manager.statuses()[0].title == "Titel"


def test_services_support_multiple_named_implementations() -> None:
    class Source(TimingSource):
        def __init__(self, source_id: str) -> None:
            self._id = source_id

        @property
        def source_id(self) -> str:
            return self._id

        @property
        def is_running(self) -> bool:
            return False

        def start(self, sink: Callable[..., None]) -> None: ...

        def stop(self) -> None: ...

    services = ServiceRegistry()
    services.register(TimingSource, Source("a"), owner="plugin_a")
    services.register(TimingSource, Source("b"), owner="plugin_b")
    assert [s.source_id for s in services.find_all(TimingSource)] == ["a", "b"]
    assert services.get(TimingSource, "plugin_b").source_id == "b"
    with pytest.raises(PluginError, match="already registered"):
        services.register(TimingSource, Source("c"), owner="plugin_a")
    services.remove_owner("plugin_a")
    assert [s.source_id for s in services.find_all(TimingSource)] == ["b"]
    with pytest.raises(ServiceNotFoundError):
        services.get(_Greeter)


def test_contribution_listeners_are_notified_and_isolated() -> None:
    registry = ContributionRegistry()
    calls: list[int] = []

    def broken() -> None:
        raise RuntimeError("listener bug")

    registry.add_listener(broken)
    remove = registry.add_listener(lambda: calls.append(1))
    registry.add_navigation("p", NavigationItem(id="x", title_key="k", order=2))
    registry.add_navigation("p", NavigationItem(id="a", title_key="k", order=1))
    assert [i.id for i in registry.navigation_items()] == ["a", "x"]
    assert calls == [1, 1]
    remove()
    registry.remove_owner("p")
    assert calls == [1, 1]
    with pytest.raises(PluginError):
        registry.add_navigation("p", NavigationItem(id="dup", title_key="k"))
        registry.add_navigation("q", NavigationItem(id="dup", title_key="k"))


def test_all_builtin_modules_are_discovered() -> None:
    result = discover_plugins()
    assert result.failures == {}
    assert {p.manifest.name for p in result.plugins} == {
        "drivers_vehicles",
        "tracks",
        "races",
        "timing",
        "timing_camera",
        "timing_sensor",
        "track_planner",
        "audio_animation",
        "statistics",
    }
