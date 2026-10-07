"""Plain-text overview placed at the top of a support bundle."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from slot_racing.core.config import AppConfig
from slot_racing.core.diagnostics.sanitize import redact


def render_report(
    facts: Mapping[str, str],
    *,
    previous_unclean: bool,
    log_notes: Sequence[str] = (),
) -> str:
    """Technical summary. Paths in ``facts`` are stored as given."""
    lines = [
        "Slot-Racing Platform Diagnostics",
        "",
        "App",
        f"Version: {facts.get('version', 'unknown')}",
        f"Commit: {facts.get('commit', 'unknown')}",
        f"Session: {facts.get('session_id', 'unknown')}",
        f"Smoke test: {facts.get('smoke_test', 'false')}",
        f"Started: {facts.get('started_at', '')}",
        "",
        "System",
        f"OS: {facts.get('os', 'unknown')}",
        f"Architecture: {facts.get('architecture', 'unknown')}",
        f"Python: {facts.get('python', 'unknown')}",
        f"PySide: {facts.get('pyside', 'unknown')}",
        f"Qt: {facts.get('qt', 'unknown')}",
        "",
        "Database",
        f"Schema revision: {facts.get('schema_revision', 'unknown')}",
        "",
        "Plugins",
        facts.get("plugins", ""),
        "",
        "Timing",
        f"Provider: {facts.get('timing_source', '')}",
        "",
        "Previous session",
        f"Clean shutdown: {'no' if previous_unclean else 'yes'}",
    ]
    if previous_unclean:
        lines.append(
            "The previous session did not record a clean shutdown. "
            "That can be a crash, a killed process or a power loss."
        )
    lines.extend(
        [
            "",
            "Logging",
            f"Directory: {facts.get('log_directory', '')}",
            f"Level: {facts.get('log_level', '')}",
            f"Rotation: {facts.get('log_rotation', '')}",
            f"Fallback: {facts.get('log_fallback', 'none')}",
        ]
    )
    if log_notes:
        lines.append("Notes:")
        lines.extend(f"- {note}" for note in log_notes)
    lines.append("")
    return "\n".join(lines)


def config_summary(config: AppConfig) -> str:
    """Sanitized settings. The database file itself is not included."""
    payload = redact(
        {
            "language": config.language,
            "timing_source": config.timing_source or "default",
            "plugin_overrides": config.plugin_overrides,
            "backup_schedule": config.backup_schedule,
            "backup_keep": config.backup_keep,
            "backup_directory": str(config.resolved_backup_directory()),
            "database_path": str(config.resolved_database_path()),
            "audio_enabled": config.audio_enabled,
            "audio_volume": config.audio_volume,
            "track_planner_color_coding": config.track_planner_color_coding,
            "track_planner_build_mode": config.track_planner_build_mode,
            "window_width": config.window_width,
            "window_height": config.window_height,
        }
    )
    lines = ["Sanitized configuration", ""]
    if isinstance(payload, dict):
        for key, value in payload.items():
            lines.append(f"{key}: {value}")
    lines.append("")
    return "\n".join(lines)
