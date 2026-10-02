from __future__ import annotations


class PluginError(Exception):
    """Base class for plugin system errors."""


class PluginDependencyError(PluginError):
    """A required plugin is missing, disabled, failed or part of a cycle."""


class PluginActivationError(PluginError):
    """A plugin raised during activation. The plugin is marked as failed."""


class ServiceNotFoundError(PluginError):
    """No service is registered for the requested interface."""
