"""Provider registry, factory contract and provider registration through plugins."""

from __future__ import annotations

from typing import ClassVar

import pytest

from slot_racing.core.clock import ManualClock
from slot_racing.core.config import AppConfig
from slot_racing.core.domain import default_timing_setup
from slot_racing.core.errors import (
    ProviderConfigurationError,
    ProviderUnavailable,
    TimingProviderError,
    ValidationError,
)
from slot_racing.core.events import EventBus
from slot_racing.core.i18n import Translator
from slot_racing.core.plugin import (
    ContributionRegistry,
    Plugin,
    PluginContext,
    PluginManager,
    PluginManifest,
    PluginState,
    ServiceRegistry,
)
from slot_racing.core.timing import (
    ProviderAvailability,
    ProviderCapabilities,
    TimingSessionSpec,
    TimingSourceFactory,
)
from slot_racing.core.timing_registry import TimingProviderRegistry
from tests.support.timing import FakeTimingFactory, FakeTimingSource


def spec(lanes: tuple[int, ...] = (1,)) -> TimingSessionSpec:
    return TimingSessionSpec(setup=default_timing_setup(), lanes=lanes, laps=2)


def registry_of(*factories: TimingSourceFactory) -> TimingProviderRegistry:
    return TimingProviderRegistry(lambda: list(factories))


def offline(provider_id: str = "pi") -> FakeTimingFactory:
    return FakeTimingFactory(
        provider_id,
        availability=ProviderAvailability.unavailable(
            "error.timing_provider.unavailable", port="tty0"
        ),
    )


def test_registered_providers_are_found_by_id_and_listed_in_order() -> None:
    camera, simulation = FakeTimingFactory("camera"), FakeTimingFactory("simulation")
    registry = registry_of(simulation, camera)
    assert registry.provider_ids() == ["camera", "simulation"]
    assert registry.factory("camera") is camera
    assert registry.is_registered("simulation") and not registry.is_registered("pi")
    assert [info.provider_id for info in registry.providers()] == ["camera", "simulation"]
    assert registry.info("camera").available


def test_an_unknown_provider_is_reported_with_its_id() -> None:
    with pytest.raises(ProviderUnavailable) as error:
        registry_of(FakeTimingFactory("simulation")).factory("camera")
    assert error.value.key == "error.timing_provider.unknown"
    assert error.value.params == {"provider": "camera"}


def test_an_empty_registry_says_that_no_provider_exists() -> None:
    with pytest.raises(ProviderUnavailable) as error:
        registry_of().check("simulation")
    assert error.value.key == "error.timing_provider.none_registered"
    assert registry_of().default_provider_id() is None


def test_duplicate_provider_ids_are_rejected() -> None:
    registry = registry_of(FakeTimingFactory("same"), FakeTimingFactory("same"))
    with pytest.raises(ProviderConfigurationError) as error:
        registry.provider_ids()
    assert error.value.key == "error.timing_provider.duplicate"


def test_blank_provider_ids_are_rejected() -> None:
    with pytest.raises(ProviderConfigurationError) as error:
        registry_of(FakeTimingFactory(" ")).provider_ids()
    assert error.value.key == "error.timing_provider.id_blank"


def test_unavailable_providers_are_listed_but_not_usable() -> None:
    registry = registry_of(FakeTimingFactory("simulation"), offline())
    infos = {info.provider_id: info for info in registry.providers()}
    assert infos["simulation"].available and not infos["pi"].available
    assert infos["pi"].availability.reason_params == {"port": "tty0"}
    with pytest.raises(ProviderUnavailable) as error:
        registry.check("pi")
    assert error.value.key == "error.timing_provider.unavailable"
    assert error.value.params == {"provider": "pi", "port": "tty0"}


def test_a_failing_availability_check_marks_the_provider_unavailable() -> None:
    registry = registry_of(FakeTimingFactory("pi", availability_error=OSError("no port")))
    info = registry.info("pi")
    assert not info.available
    assert info.availability.reason_key == "error.timing_provider.failed"
    assert "no port" in str(info.availability.reason_params["detail"])
    with pytest.raises(ProviderUnavailable):
        registry.check("pi")


def test_default_provider_prefers_the_configured_available_one() -> None:
    registry = registry_of(FakeTimingFactory("b"), FakeTimingFactory("a"), offline("c"))
    assert registry.default_provider_id() == "a"
    assert registry.default_provider_id("b") == "b"
    assert registry.default_provider_id("c") == "a"
    assert registry.default_provider_id("missing") == "a"


def test_lane_capability_is_checked_before_the_race_starts() -> None:
    single = FakeTimingFactory(
        "single", capabilities=ProviderCapabilities(supports_multiple_lanes=False)
    )
    registry = registry_of(single)
    registry.check("single", lane_count=1)
    with pytest.raises(ProviderConfigurationError) as error:
        registry.check("single", lane_count=2)
    assert error.value.key == "error.timing_provider.single_lane"
    with pytest.raises(ProviderConfigurationError):
        registry.create_source("single", spec(lanes=(1, 2)))


def test_create_source_uses_the_factory_with_the_session_spec() -> None:
    factory = FakeTimingFactory("fake")
    source = registry_of(factory).create_source("fake", spec((1, 2)))
    assert source is factory.source
    assert factory.specs[0].lanes == (1, 2)
    assert factory.specs[0].setup == default_timing_setup()
    assert not source.is_running


def test_an_unsupported_setup_is_reported_as_configuration_error() -> None:
    error = ProviderConfigurationError("error.timing_provider.single_lane", provider="x")
    registry = registry_of(FakeTimingFactory("fake", validate_error=error))
    with pytest.raises(ProviderConfigurationError) as caught:
        registry.create_source("fake", spec())
    assert caught.value is error


def test_an_unexpected_validation_failure_is_wrapped() -> None:
    registry = registry_of(FakeTimingFactory("fake", validate_error=KeyError("pin")))
    with pytest.raises(ProviderConfigurationError) as caught:
        registry.check("fake", spec=spec())
    assert caught.value.key == "error.timing_provider.failed"
    assert "KeyError" in str(caught.value.params["detail"])


def test_a_provider_error_of_the_factory_is_passed_on_unchanged() -> None:
    error = ProviderUnavailable("error.timing_provider.unavailable", provider="fake")
    registry = registry_of(FakeTimingFactory("fake", create_error=error))
    with pytest.raises(ProviderUnavailable) as caught:
        registry.create_source("fake", spec())
    assert caught.value is error


def test_an_unexpected_factory_crash_becomes_a_translatable_error() -> None:
    registry = registry_of(FakeTimingFactory("fake", create_error=RuntimeError("boom")))
    with pytest.raises(TimingProviderError) as caught:
        registry.create_source("fake", spec())
    assert isinstance(caught.value, ValidationError)
    assert caught.value.key == "error.timing_provider.failed"
    assert caught.value.params["provider"] == "fake"
    assert "boom" in str(caught.value.params["detail"])


def test_the_default_factory_contract_is_permissive() -> None:
    factory = FakeTimingFactory("fake")
    assert factory.capabilities == ProviderCapabilities()
    assert ProviderCapabilities().supports_multiple_lanes
    assert not ProviderCapabilities().supports_test_mode
    assert ProviderAvailability.ok().available


class Harness:
    def __init__(self) -> None:
        self.services = ServiceRegistry()
        self.manager = PluginManager(
            bus=EventBus(),
            services=self.services,
            contributions=ContributionRegistry(),
            translator=Translator(),
            clock=ManualClock(),
            config=AppConfig(),
        )
        self.registry = TimingProviderRegistry(lambda: self.services.find_all(TimingSourceFactory))


def provider_plugin(name: str, provider_id: str) -> Plugin:
    class _Provider(Plugin):
        manifest: ClassVar[PluginManifest] = PluginManifest(
            name=name, version="1", title=name, enabled_by_default=True
        )

        def activate(self, context: PluginContext) -> None:
            context.register_timing_provider(FakeTimingFactory(provider_id))

    return _Provider()


def test_plugins_register_providers_without_the_race_module_knowing_them() -> None:
    harness = Harness()
    for name, provider_id in (("cam", "camera"), ("pi", "raspberry_pi")):
        harness.manager.register(provider_plugin(name, provider_id))
    harness.manager.enable_many(["cam", "pi"])
    assert harness.registry.provider_ids() == ["camera", "raspberry_pi"]


def test_disabling_the_plugin_removes_its_provider() -> None:
    harness = Harness()
    harness.manager.register(provider_plugin("cam", "camera"))
    harness.manager.enable_many(["cam"])
    harness.manager.disable("cam")
    assert harness.registry.provider_ids() == []
    harness.manager.enable("cam")
    assert harness.registry.provider_ids() == ["camera"]


def test_two_plugins_cannot_claim_the_same_provider_id() -> None:
    harness = Harness()
    harness.manager.register(provider_plugin("first", "camera"))
    harness.manager.register(provider_plugin("second", "camera"))
    harness.manager.enable_many(["first", "second"])
    states = {status.name: status.state for status in harness.manager.statuses()}
    assert states == {"first": PluginState.ENABLED, "second": PluginState.FAILED}
    assert harness.registry.provider_ids() == ["camera"]


def test_fake_source_records_the_lifecycle() -> None:
    source = FakeTimingSource()
    source.start(lambda event: None)
    source.pause()
    source.resume()
    source.stop()
    source.emit(1, "a")
    assert source.calls == ["start", "pause", "resume", "stop"]
