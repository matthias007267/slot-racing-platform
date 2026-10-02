"""Display texts for timing providers. The provider id is a technical identifier; the label comes
from the translation ``timing.provider.<id>`` (a provider module registers it) and falls back to
the id."""

from __future__ import annotations

from slot_racing.core.i18n import Translator
from slot_racing.core.timing_registry import TimingProviderInfo


def provider_label(translator: Translator, provider_id: str) -> str:
    return translator.translate(f"timing.provider.{provider_id}", default=provider_id)


def availability_text(translator: Translator, info: TimingProviderInfo) -> str:
    """``verfügbar`` or the provider's translated reason for being unavailable."""
    availability = info.availability
    if availability.available:
        return translator.translate("timing.provider.available")
    if availability.reason_key is None:
        return translator.translate("timing.provider.unavailable")
    return translator.format(
        availability.reason_key, **{"provider": info.provider_id, **availability.reason_params}
    )


def provider_item_text(translator: Translator, info: TimingProviderInfo) -> str:
    """Selection entry: the label, with the state appended when the provider is unusable."""
    label = provider_label(translator, info.provider_id)
    if info.available:
        return label
    return f"{label} - {translator.translate('timing.provider.unavailable')}"
