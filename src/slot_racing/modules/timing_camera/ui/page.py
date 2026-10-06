"""Global camera setup page.

The page edits the saved camera document: device, resolution, frame rate,
travel direction, sensitivity and detection zones. Block size stays at the
stored value; it is not a control on this page. The picture is a configuration
preview. It is not scanned for cars: lane detection starts with the camera
race, not while the page is open.
The page does not start a race and does not publish sensor events. Leaving the
page stops the preview and releases the device.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable, Sequence
from contextlib import suppress
from dataclasses import dataclass
from typing import Protocol

from pydantic import ValidationError
from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QComboBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QPushButton,
    QSizePolicy,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from slot_racing.core.domain.lanes import DEFAULT_LANE_COUNT, MAX_LANE_COUNT, MIN_LANE_COUNT
from slot_racing.core.errors import ValidationError as InputError
from slot_racing.core.i18n import Translator
from slot_racing.modules.timing_camera.camera_config import CameraConfig
from slot_racing.modules.timing_camera.capture import CameraOpenError
from slot_racing.modules.timing_camera.configuration import (
    CameraConfiguration,
    NormalizedRoi,
    StoredCamera,
    StoredDetection,
    StoredDetectionZone,
)
from slot_racing.modules.timing_camera.detection import TravelDirection
from slot_racing.modules.timing_camera.frame_source import FrameSource
from slot_racing.modules.timing_camera.frames import GrayFrame
from slot_racing.modules.timing_camera.lease import CameraBusyError
from slot_racing.modules.timing_camera.store import CameraConfigurationError
from slot_racing.modules.timing_camera.ui.stage import CameraStage
from slot_racing.uikit import StatusLabel, describe_error
from slot_racing.uikit.theme import configure_page, set_role, set_tone

logger = logging.getLogger(__name__)

_PREVIEW_INTERVAL_MS = 50
_DEFAULT_POSITION = "start_finish"
_RESOLUTIONS = ((320, 240), (640, 480), (800, 600), (1280, 720), (1920, 1080))
_FRAME_RATES = (15, 30, 60)
_State = tuple[object, ...]


class CameraSetupStore(Protocol):
    """The saved document. The page never opens a database session itself."""

    def load(self) -> CameraConfiguration:
        """The last saved configuration, or the defaults when nothing is stored."""

    def save(self, configuration: CameraConfiguration) -> None:
        """Replace the saved document."""


class CameraSetupPreview(Protocol):
    """Opens a preview source. A test double stands in for the device."""

    def device_indices(self) -> tuple[int, ...]:
        """Device indices that can be opened right now."""

    def open(self, config: CameraConfig) -> FrameSource:
        """Start a preview. Raise when the device is missing or already taken."""


@dataclass
class ZoneDraft:
    """One zone while it is being edited. Geometry stays normalized."""

    position_id: str
    lane: int
    roi: NormalizedRoi


class CameraSetupPage(QWidget):
    """Edit the global camera configuration on top of a live picture."""

    def __init__(
        self,
        translator: Translator,
        store: CameraSetupStore,
        preview: CameraSetupPreview,
        lane_limit: Callable[[], int] | None = None,
    ) -> None:
        super().__init__()
        self.setObjectName("camera-setup")
        self._translator = translator
        self._store = store
        self._preview = preview
        self._lane_limit = lane_limit or (lambda: MAX_LANE_COUNT)
        self._drafts: list[ZoneDraft] = []
        self._block_size = 20
        self._snapshot: _State = ()
        self._source: FrameSource | None = None
        self._loading = False
        self._drawing = False
        self._corrupt = False
        self._saved_flash = False
        self._error_key: str | None = None
        self._detail: str | None = None
        self._camera_state = "unknown"
        self._timer = QTimer(self)
        self._timer.setInterval(_PREVIEW_INTERVAL_MS)
        self._timer.timeout.connect(self._pull_frame)
        self.destroyed.connect(self._stop_preview)

        self.stage = CameraStage()
        self.stage.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.device = QComboBox()
        self.device.setObjectName("camera-device")
        self.resolution = QComboBox()
        self.resolution.setObjectName("camera-resolution")
        self.fps = QComboBox()
        self.fps.setObjectName("camera-fps")
        self.direction = QComboBox()
        self.direction.setObjectName("camera-direction")
        self.sensitivity = QSlider(Qt.Orientation.Horizontal)
        self.sensitivity.setObjectName("camera-sensitivity")
        self.sensitivity.setRange(0, 100)
        self.sensitivity_less = QLabel(self._tr("camera.sensitivity.less"))
        self.sensitivity_more = QLabel(self._tr("camera.sensitivity.more"))
        self.zones = QListWidget()
        self.zones.setObjectName("camera-zones")
        self.position = QLineEdit()
        self.position.setObjectName("camera-position")
        self.add_zone = QPushButton(self._tr("camera.action.add"))
        self.add_zone.setObjectName("camera-add-zone")
        self.delete_zone = QPushButton(self._tr("camera.action.delete"))
        self.delete_zone.setObjectName("camera-delete-zone")
        self.refresh = QPushButton(self._tr("camera.action.refresh"))
        self.refresh.setObjectName("camera-refresh")
        self.cancel = QPushButton(self._tr("camera.action.cancel"))
        self.cancel.setObjectName("camera-cancel")
        self.save = QPushButton(self._tr("camera.action.save"))
        self.save.setObjectName("camera-save")
        set_role(self.add_zone, "secondary")
        set_role(self.delete_zone, "danger")
        set_role(self.refresh, "secondary")
        set_role(self.cancel, "ghost")
        set_role(self.save, "primary")
        self.status = QLabel()
        self.status.setObjectName("camera-status")
        self.message = StatusLabel("camera-message")

        camera_form = QFormLayout()
        camera_form.addRow(self._tr("camera.field.device"), self.device)
        camera_form.addRow(self._tr("camera.field.resolution"), self.resolution)
        camera_form.addRow(self._tr("camera.field.fps"), self.fps)
        camera_form.addRow(self._tr("camera.field.direction"), self.direction)
        sensitivity_row = QHBoxLayout()
        sensitivity_row.addWidget(self.sensitivity_less)
        sensitivity_row.addWidget(self.sensitivity, 1)
        sensitivity_row.addWidget(self.sensitivity_more)
        camera_form.addRow(self._tr("camera.field.sensitivity"), sensitivity_row)
        zone_form = QFormLayout()
        zone_form.addRow(self._tr("camera.field.position"), self.position)

        side = QWidget()
        side.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Expanding)
        side_layout = QVBoxLayout(side)
        side_layout.addWidget(_section(self._tr("camera.section.camera")))
        side_layout.addLayout(camera_form)
        side_layout.addWidget(self.refresh)
        side_layout.addWidget(_section(self._tr("camera.section.zones")))
        side_layout.addWidget(self.zones, 1)
        side_layout.addLayout(zone_form)
        side_layout.addWidget(self.add_zone)
        side_layout.addWidget(self.delete_zone)

        body = QHBoxLayout()
        body.addWidget(self.stage, 1)
        body.addWidget(side)
        bar = QHBoxLayout()
        bar.addWidget(self.status)
        bar.addWidget(self.message, 1)
        bar.addWidget(self.cancel)
        bar.addWidget(self.save)
        root = QVBoxLayout(self)
        configure_page(root)
        root.addLayout(body, 1)
        root.addLayout(bar)

        self.device.currentIndexChanged.connect(self._on_camera_changed)
        self.resolution.currentIndexChanged.connect(self._on_camera_changed)
        self.fps.currentIndexChanged.connect(self._on_camera_changed)
        self.direction.currentIndexChanged.connect(self._note_edit)
        self.sensitivity.valueChanged.connect(self._note_edit)
        self.zones.currentRowChanged.connect(self._on_list)
        self.position.textChanged.connect(self._on_position)
        self.add_zone.clicked.connect(self._on_add)
        self.delete_zone.clicked.connect(self._on_delete)
        self.refresh.clicked.connect(self._on_refresh)
        self.cancel.clicked.connect(self._on_cancel)
        self.save.clicked.connect(self._on_save)
        self.stage.selection_changed.connect(self._on_stage_selection)
        self.stage.geometry_changed.connect(self._on_geometry)
        self.stage.zone_drawn.connect(self._on_zone_drawn)
        self.stage.draw_rejected.connect(self._on_draw_rejected)
        self._apply(self._read_store(), restart=False)

    def showEvent(self, event: object) -> None:  # noqa: N802
        super().showEvent(event)  # type: ignore[arg-type]
        self._start_preview()

    def hideEvent(self, event: object) -> None:  # noqa: N802
        self._stop_preview()
        super().hideEvent(event)  # type: ignore[arg-type]

    def closeEvent(self, event: object) -> None:  # noqa: N802
        self._stop_preview()
        super().closeEvent(event)  # type: ignore[arg-type]

    def _tr(self, key: str) -> str:
        return self._translator.translate(key)

    def _read_store(self) -> CameraConfiguration:
        try:
            loaded = self._store.load()
        except CameraConfigurationError:
            self._corrupt = True
            return CameraConfiguration()
        except Exception as error:
            logger.exception("Could not load the camera configuration")
            self._corrupt = True
            self._detail = describe_error(self._translator, error)
            return CameraConfiguration()
        self._corrupt = False
        return loaded

    def _apply(self, config: CameraConfiguration, *, restart: bool) -> None:
        self._loading = True
        self._drafts = [
            ZoneDraft(zone.position_id, zone.lane, zone.roi) for zone in config.detection.zones
        ]
        self._block_size = config.detection.block_size
        self._fill_direction(config.detection.direction)
        self.sensitivity.setValue(config.detection.sensitivity)
        self._fill_devices(self._probe(), config.camera.device_index)
        self._fill_choices(
            self.resolution,
            self._resolution_items(config.camera.width, config.camera.height),
            f"{config.camera.width}x{config.camera.height}",
        )
        self._fill_choices(self.fps, self._fps_items(config.camera.fps), config.camera.fps)
        self._loading = False
        self._show_drafts(0 if self._drafts else -1)
        self._snapshot = self._state()
        if restart and self.isVisible():
            self._start_preview()
        else:
            self._refresh_status()

    def _probe(self) -> tuple[int, ...]:
        try:
            found = self._preview.device_indices()
        except Exception:
            logger.exception("Could not list cameras")
            return ()
        indices: list[int] = []
        for item in found:
            if isinstance(item, bool) or not isinstance(item, int) or item < 0:
                continue
            indices.append(item)
        return tuple(dict.fromkeys(indices))

    def _fill_devices(self, found: tuple[int, ...], selected: int) -> None:
        indices = tuple(sorted(set(found) | {selected}))
        items = [
            (self._translator.format("camera.device", index=index), index) for index in indices
        ]
        self._fill_choices(self.device, items, selected)

    def _resolution_items(self, width: int, height: int) -> list[tuple[str, str]]:
        sizes = list(_RESOLUTIONS)
        if (width, height) not in sizes:
            sizes.append((width, height))
        return [
            (
                self._translator.format("camera.resolution", width=item_width, height=item_height),
                f"{item_width}x{item_height}",
            )
            for item_width, item_height in sizes
        ]

    def _fill_direction(self, selected: TravelDirection) -> None:
        items = [
            (self._tr("camera.direction.top_to_bottom"), TravelDirection.TOP_TO_BOTTOM.value),
            (self._tr("camera.direction.bottom_to_top"), TravelDirection.BOTTOM_TO_TOP.value),
            (self._tr("camera.direction.left_to_right"), TravelDirection.LEFT_TO_RIGHT.value),
            (self._tr("camera.direction.right_to_left"), TravelDirection.RIGHT_TO_LEFT.value),
        ]
        self._fill_choices(self.direction, items, selected.value)

    def _fps_items(self, fps: int) -> list[tuple[str, int]]:
        rates = list(_FRAME_RATES)
        if fps not in rates:
            rates.append(fps)
        return [(self._translator.format("camera.fps", fps=rate), rate) for rate in rates]

    def _fill_choices(
        self,
        combo: QComboBox,
        items: Sequence[tuple[str, object]],
        selected: object,
    ) -> None:
        combo.blockSignals(True)
        combo.clear()
        chosen = 0
        for index, (text, value) in enumerate(items):
            combo.addItem(text, value)
            if value == selected:
                chosen = index
        combo.setCurrentIndex(chosen)
        combo.blockSignals(False)

    def _show_drafts(self, select: int) -> None:
        self._loading = True
        self.stage.set_zones([(draft.roi, self._label(draft)) for draft in self._drafts])
        self.zones.blockSignals(True)
        self.zones.clear()
        for index in range(len(self._drafts)):
            self.zones.addItem(self._list_text(index))
        if 0 <= select < self.zones.count():
            self.zones.setCurrentRow(select)
        else:
            self.zones.setCurrentRow(-1)
        self.stage.set_selected(select)
        self._fill_editors()
        self.zones.blockSignals(False)
        self._loading = False

    def _fill_editors(self) -> None:
        index = self.stage.selected_index()
        enabled = 0 <= index < len(self._drafts)
        self.position.setEnabled(enabled)
        self.delete_zone.setEnabled(enabled)
        previous = self._loading
        self._loading = True
        if enabled:
            self.position.setText(self._drafts[index].position_id)
        else:
            self.position.clear()
        self._loading = previous

    def _label(self, draft: ZoneDraft) -> str:
        if not _complete(draft.position_id):
            return self._tr("camera.zone.incomplete")
        return self._translator.format(
            "camera.zone.label", position=draft.position_id, lane=draft.lane
        )

    def _list_text(self, index: int) -> str:
        number = self._translator.format("camera.zone.number", number=index + 1)
        return f"{number}: {self._label(self._drafts[index])}"

    def _device_index(self) -> int:
        value = self.device.currentData()
        if isinstance(value, int) and not isinstance(value, bool):
            return value
        return 0

    def _resolution(self) -> tuple[int, int]:
        value = self.resolution.currentData()
        if isinstance(value, str):
            width_text, separator, height_text = value.partition("x")
            if separator and width_text.isdigit() and height_text.isdigit():
                return int(width_text), int(height_text)
        return (640, 480)

    def _frame_rate(self) -> int:
        value = self.fps.currentData()
        if isinstance(value, int) and not isinstance(value, bool):
            return value
        return 30

    def _camera_config(self) -> CameraConfig:
        width, height = self._resolution()
        return CameraConfig(
            device_index=self._device_index(),
            width=width,
            height=height,
            fps=self._frame_rate(),
        )

    def _direction(self) -> TravelDirection:
        value = self.direction.currentData()
        if isinstance(value, str):
            try:
                return TravelDirection(value)
            except ValueError:
                return TravelDirection.LEFT_TO_RIGHT
        return TravelDirection.LEFT_TO_RIGHT

    def _state(self) -> _State:
        width, height = self._resolution()
        zones = tuple(
            (
                draft.position_id,
                draft.lane,
                draft.roi.x,
                draft.roi.y,
                draft.roi.width,
                draft.roi.height,
            )
            for draft in self._drafts
        )
        return (
            self._device_index(),
            width,
            height,
            self._frame_rate(),
            self._direction(),
            self.sensitivity.value(),
            self._block_size,
            zones,
        )

    def _is_dirty(self) -> bool:
        return self._state() != self._snapshot

    def _document(self) -> CameraConfiguration:
        width, height = self._resolution()
        return CameraConfiguration(
            camera=StoredCamera(
                device_index=self._device_index(),
                width=width,
                height=height,
                fps=self._frame_rate(),
            ),
            detection=StoredDetection(
                zones=tuple(
                    StoredDetectionZone(
                        position_id=draft.position_id, lane=draft.lane, roi=draft.roi
                    )
                    for draft in self._drafts
                ),
                block_size=self._block_size,
                sensitivity=self.sensitivity.value(),
                direction=self._direction(),
            ),
        )

    def _note_edit(self) -> None:
        if self._loading:
            return
        self._saved_flash = False
        self._error_key = None
        self._detail = None
        self._refresh_status()

    def _on_camera_changed(self) -> None:
        if self._loading:
            return
        self._saved_flash = False
        self._error_key = None
        self._detail = None
        if self.isVisible():
            self._start_preview()
        else:
            self._refresh_status()

    def _on_list(self, row: int) -> None:
        if self._loading:
            return
        self.stage.set_selected(row)
        self._fill_editors()

    def _on_stage_selection(self, index: int) -> None:
        if self._loading:
            return
        self._loading = True
        self.zones.setCurrentRow(index)
        self._loading = False
        self._fill_editors()

    def _on_position(self, text: str) -> None:
        if self._loading:
            return
        index = self.stage.selected_index()
        if index < 0 or index >= len(self._drafts):
            return
        self._drafts[index].position_id = text
        self.stage.set_label(index, self._label(self._drafts[index]))
        item = self.zones.item(index)
        if item is not None:
            item.setText(self._list_text(index))
        self._note_edit()

    def _current_lane_limit(self) -> int:
        try:
            reported = self._lane_limit()
        except Exception:
            logger.exception("Could not read the track lane count")
            return MAX_LANE_COUNT
        if isinstance(reported, bool) or not isinstance(reported, int):
            return MAX_LANE_COUNT
        return max(1, min(MAX_LANE_COUNT, reported))

    def _on_add(self) -> None:
        self._error_key = None
        self._detail = None
        limit = self._current_lane_limit()
        used = {draft.lane for draft in self._drafts}
        lane = next((number for number in range(1, limit + 1) if number not in used), None)
        if lane is None or len(self._drafts) >= limit:
            self._error_key = "camera.status.too_many_zones"
            self._drawing = False
            self._refresh_status()
            return
        self._drafts.append(ZoneDraft(_DEFAULT_POSITION, lane, _default_roi(lane)))
        self._drawing = False
        self._show_drafts(len(self._drafts) - 1)
        self._note_edit()

    def _on_delete(self) -> None:
        index = self.stage.selected_index()
        if index < 0 or index >= len(self._drafts):
            return
        del self._drafts[index]
        self._show_drafts(min(index, len(self._drafts) - 1))
        self._note_edit()

    def _on_refresh(self) -> None:
        selected = self._device_index()
        self._loading = True
        self._fill_devices(self._probe(), selected)
        self._loading = False
        if self.isVisible():
            self._start_preview()
        else:
            self._refresh_status()

    def _on_geometry(self) -> None:
        for draft, roi in zip(self._drafts, self.stage.zones(), strict=False):
            draft.roi = roi
        self._note_edit()

    def _on_zone_drawn(self) -> None:
        zones = self.stage.zones()
        if len(zones) != len(self._drafts) + 1:
            return
        self._drafts.append(ZoneDraft("", 1, zones[-1]))
        self._drawing = False
        self._show_drafts(len(self._drafts) - 1)
        self._note_edit()

    def _on_draw_rejected(self) -> None:
        self._drawing = False
        self._error_key = "camera.status.too_small"
        self._detail = None
        self._refresh_status()

    def _on_cancel(self) -> None:
        self._error_key = None
        self._detail = None
        self._saved_flash = False
        self._drawing = False
        self._apply(self._read_store(), restart=True)

    def save_persistent(self) -> None:
        """Write a camera draft that has not been saved yet. A clean page is left untouched."""
        if not self._is_dirty():
            return
        if any(not _complete(draft.position_id) for draft in self._drafts):
            raise InputError("camera.status.incomplete")
        try:
            document = self._document()
        except ValidationError as error:
            raise InputError("camera.status.invalid") from error
        try:
            self._store.save(document)
        except CameraConfigurationError as error:
            raise InputError("camera.status.save_failed") from error
        self._snapshot = self._state()
        self._corrupt = False
        self._error_key = None
        self._detail = None

    def _on_save(self) -> None:
        if any(not _complete(draft.position_id) for draft in self._drafts):
            self._error_key = "camera.status.incomplete"
            self._detail = None
            self._refresh_status()
            return
        try:
            document = self._document()
        except ValidationError:
            self._error_key = "camera.status.invalid"
            self._detail = None
            self._refresh_status()
            return
        try:
            self._store.save(document)
        except CameraConfigurationError:
            self._error_key = "camera.status.save_failed"
            self._detail = None
            self._refresh_status()
            return
        except Exception as error:
            logger.exception("Could not save the camera configuration")
            self._error_key = None
            self._detail = describe_error(self._translator, error)
            self._refresh_status()
            return
        self._snapshot = self._state()
        self._corrupt = False
        self._error_key = None
        self._detail = None
        self._saved_flash = True
        self._refresh_status()

    def _start_preview(self) -> None:
        self._stop_preview()
        try:
            config = self._camera_config()
        except (TypeError, ValueError):
            self._camera_state = "offline"
            self._refresh_status()
            return
        self.stage.set_frame(GrayFrame.blank(config.width, config.height))
        try:
            self._source = self._preview.open(config)
        except CameraBusyError:
            self._camera_state = "busy"
            self._refresh_status()
            return
        except CameraOpenError:
            self._camera_state = "offline"
            self._refresh_status()
            return
        except Exception as error:
            logger.exception("Could not open the camera preview")
            self._camera_state = "offline"
            self._detail = describe_error(self._translator, error)
            self._refresh_status()
            return
        self._camera_state = "live"
        self._pull_frame()
        self._timer.start()
        self._refresh_status()

    def _pull_frame(self) -> None:
        source = self._source
        if source is None:
            return
        try:
            delivered = source.poll_latest()
        except Exception as error:
            logger.exception("Camera preview stopped delivering frames")
            self._stop_preview()
            self._camera_state = "offline"
            self._detail = describe_error(self._translator, error)
            self._refresh_status()
            return
        if delivered is not None:
            self.stage.set_frame(delivered.frame)

    def _stop_preview(self, *_args: object) -> None:
        timer = getattr(self, "_timer", None)
        if timer is not None:
            with suppress(RuntimeError):
                timer.stop()
        source = getattr(self, "_source", None)
        self._source = None
        if source is None:
            return
        try:
            source.stop()
        except Exception:
            logger.exception("Camera preview did not stop")

    def _refresh_status(self) -> None:
        if self._camera_state == "live":
            lamp = f"Status: ● {self._tr('camera.status.live')}"
            tone = "ok"
        elif self._camera_state == "busy":
            lamp = f"Status: ○ {self._tr('camera.status.offline')}"
            tone = "warn"
        else:
            lamp = f"Status: ○ {self._tr('camera.status.offline')}"
            tone = "error"
        self.status.setText(lamp)
        set_tone(self.status, tone)
        if self._error_key is not None:
            self.message.show_error(self._tr(self._error_key))
            return
        if self._detail is not None:
            self.message.show_error(self._detail)
            return
        if self._corrupt and not self._is_dirty():
            self.message.show_error(self._tr("error.timing_provider.camera_configuration_invalid"))
            return
        if self._drawing:
            self.message.show_info(self._tr("camera.status.draw"))
            return
        if self._is_dirty():
            self.message.show_info(self._tr("camera.status.dirty"))
            return
        if self._saved_flash:
            self.message.show_info(self._tr("camera.status.saved"))
            return
        if self._camera_state == "offline":
            self.message.show_error(self._tr("camera.status.offline_detail"))
            return
        if self._camera_state == "busy":
            self.message.show_error(self._tr("camera.status.busy"))
            return
        self.message.clear_message()


def _section(text: str) -> QLabel:
    label = QLabel(text)
    set_role(label, "section")
    return label


def _complete(position_id: str) -> bool:
    return position_id.strip() != "" and position_id == position_id.strip()


def zone_limit(lane_counts: Iterable[int]) -> int:
    """How many camera zones the current tracks allow.

    The widest active track decides. The result stays inside the lane choices
    the application offers for a new track, and it is never above four.
    """
    counts = tuple(lane_counts)
    if not counts:
        return DEFAULT_LANE_COUNT
    widest = max(counts)
    return min(MAX_LANE_COUNT, max(MIN_LANE_COUNT, widest))


def _default_roi(lane: int) -> NormalizedRoi:
    """A rectangle the user can drag. Each lane starts on its own row."""
    top = min(0.75, 0.08 + (lane - 1) * 0.2)
    return NormalizedRoi(x=0.35, y=top, width=0.3, height=0.12)
