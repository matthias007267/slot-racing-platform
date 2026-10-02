# 0009 Timing providers: ids, registry, capabilities, availability and errors

## Context
`TimingSource`, `TimingSourceFactory` and `SensorTriggered` exist (ADR 0004, 0007, 0008), but the
factory was selected by an ambiguous `name`, nothing told whether a provider was usable before a
race started, the race did not know its provider and the timing test mode picked a factory by
name. Camera, Raspberry Pi and Carrera providers are planned and must be addable without
touching the race engine, the race module, the database model or the UI.

## Decision
- **Provider id.** `TimingSourceFactory.name` becomes `provider_id`, a stable technical id
  (`simulation`, later `camera`, `raspberry_pi`, ...). Labels are translations
  `timing.provider.<id>`.
- **Registration.** Providers register through `PluginContext.register_timing_provider`, which
  uses the existing service registry (named by `provider_id`, owner removal on disable, duplicate
  ids rejected). No separate state is introduced.
- **Registry.** `TimingProviderRegistry` is a thin core service that looks up and checks those
  registrations (`providers`, `factory`, `check`, `create_source`, `default_provider_id`). It
  replaces the list of factories that the race controller received.
- **Availability and capabilities.** The factory exposes `availability()`, `validate(spec)` and
  `capabilities` with only the two capabilities the application uses (`supports_test_mode`,
  `supports_multiple_lanes`). Callers use them instead of provider names.
- **Errors.** `TimingProviderError(ValidationError)` with `ProviderUnavailable` and
  `ProviderConfigurationError`; translatable, raised before the race starts. Runtime failures of a
  running source stay isolated in the engine (`source_errors`).
- **Race.** `races.timing_provider` (migration `0004`, default `simulation`) stores the id; the
  factory is resolved at the start. The race wizard offers the registered providers, shows
  unavailable ones as not selectable, and `validate_startable` runs the preflight.
- **Time base and lifecycle** are documented in `docs/architecture.md`: integer nanoseconds on the
  host's monotonic clock, stamped by the source; `start`, `poll`, `pause`, `resume`, `stop`.
- **Test mode** picks the first available provider with `supports_test_mode`.
- **Not decided here:** a sequence number or provider field on `SensorTriggered`; `source_id` and
  the timestamp are sufficient for the engine.

## Consequences
- A new provider is one plugin: factory, source, translation and `register_timing_provider`.
- Existing races read as `simulation`; races that name a provider that is not installed can be
  shown and deleted but not started, with a clear message.
- `AppConfig.timing_source` now only preselects the provider of new races.
- Tests use `FakeTimingSource`/`FakeTimingFactory` instead of the simulation.
