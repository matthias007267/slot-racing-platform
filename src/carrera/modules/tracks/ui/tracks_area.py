"""Track management: the track list, the timing configuration of a track, the wizard and the
test mode, as one navigation page."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtWidgets import QStackedWidget, QVBoxLayout, QWidget

from carrera.core.catalog import TrackInfo
from carrera.core.clock import Clock
from carrera.core.domain import TrackId
from carrera.core.i18n import Translator
from carrera.core.timing import TimingSetupService
from carrera.core.timing_registry import TimingProviderRegistry
from carrera.modules.tracks.service import TrackService
from carrera.modules.tracks.ui.common import run_guarded
from carrera.modules.tracks.ui.timing_test_view import TimingTestView
from carrera.modules.tracks.ui.timing_view import TimingConfigView
from carrera.modules.tracks.ui.timing_wizard import TimingWizard
from carrera.modules.tracks.ui.tracks_page import TracksPage


class TracksArea(QWidget):
    def __init__(
        self,
        translator: Translator,
        service: TrackService,
        setups: Callable[[], TimingSetupService | None],
        providers: TimingProviderRegistry,
        clock: Clock,
    ) -> None:
        super().__init__()
        self._service = service
        self.tracks_page = TracksPage(translator, service)
        self.config_view = TimingConfigView(translator, setups)
        self.test_view = TimingTestView(translator, providers, clock)
        self.wizard = TimingWizard(translator, service.list_tracks, setups, providers, clock)
        self.stack = QStackedWidget()
        for page in (self.tracks_page, self.config_view, self.test_view, self.wizard):
            self.stack.addWidget(page)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.stack)

        self.tracks_page.timing_requested.connect(self.show_config)
        self.config_view.back_requested.connect(self.show_tracks)
        self.config_view.test_requested.connect(self.show_test)
        self.config_view.wizard_requested.connect(self._wizard_for_current_track)
        self.test_view.back_requested.connect(lambda: self.stack.setCurrentWidget(self.config_view))
        self.wizard.cancelled.connect(self.show_tracks)
        self.wizard.finished.connect(self._wizard_finished)

    def current_page(self) -> QWidget | None:
        return self.stack.currentWidget()

    def show_tracks(self) -> None:
        self.stack.setCurrentWidget(self.tracks_page)

    def show_config(self, track_id: int) -> None:
        track = self._service.get_track(TrackId(track_id))
        if track is None:
            self.tracks_page.refresh()
            return
        self.config_view.open_track(track)
        self.stack.setCurrentWidget(self.config_view)

    def show_test(self) -> None:
        """Open the test mode for the configuration as currently edited."""

        def open_test() -> None:
            setup = self.config_view.current_setup()
            track = self.config_view.track
            self.test_view.open_setup(setup, track.id if track else None)
            self.stack.setCurrentWidget(self.test_view)

        run_guarded(
            self.config_view.translator, self.config_view.status, self.config_view, open_test
        )

    def show_wizard(self, track: TrackInfo | None = None) -> None:
        self.wizard.start(track)
        self.stack.setCurrentWidget(self.wizard)

    def _wizard_for_current_track(self) -> None:
        self.show_wizard(self.config_view.track)

    def _wizard_finished(self, track_id: int) -> None:
        self.show_config(track_id)
