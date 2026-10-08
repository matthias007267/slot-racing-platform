"""Championship list, calendar, standings and the stored points scheme."""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import date

from PySide6.QtCore import QDate, Qt
from PySide6.QtGui import QShowEvent
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDateEdit,
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from slot_racing.core.catalog import DriverCatalog
from slot_racing.core.championship import RaceScore
from slot_racing.core.domain import RaceStatus
from slot_racing.core.errors import ValidationError
from slot_racing.core.i18n import Translator
from slot_racing.modules.championships.service import (
    ACTIVE,
    COMPLETED,
    PLANNED,
    ChampionshipBoard,
    ChampionshipInfo,
    ChampionshipInput,
    ChampionshipService,
    LinkedRace,
)
from slot_racing.uikit import FormDialog
from slot_racing.uikit.career import MISSING
from slot_racing.uikit.errors import describe_error, is_expected
from slot_racing.uikit.report_view import RaceReportView
from slot_racing.uikit.theme import SPACE, configure_page, set_role
from slot_racing.uikit.widgets import (
    StatusLabel,
    fill_sortable,
    fill_table,
    make_table,
    selected_id,
)

logger = logging.getLogger(__name__)


def _ask_yes_no(parent: QWidget, title: str, text: str) -> bool:
    answer = QMessageBox.question(parent, title, text)
    return answer == QMessageBox.StandardButton.Yes


def _date_edit() -> QDateEdit:
    edit = QDateEdit()
    edit.setCalendarPopup(True)
    edit.setDisplayFormat("dd.MM.yyyy")
    edit.setDate(QDate.currentDate())
    return edit


def _python_date(edit: QDateEdit) -> date:
    value = edit.date()
    return date(value.year(), value.month(), value.day())


def _show_date(edit: QDateEdit, value: date | None) -> None:
    shown = QDate.currentDate() if value is None else QDate(value.year, value.month, value.day)
    edit.setDate(shown)


class ChampionshipDialog(FormDialog):
    """Name, season, optional period and whether a team table is kept."""

    def __init__(
        self,
        translator: Translator,
        service: ChampionshipService,
        current: ChampionshipInfo | None,
        parent: QWidget | None = None,
    ) -> None:
        tr = translator.translate
        super().__init__(
            translator,
            tr("championship.dialog.edit" if current else "championship.dialog.new"),
            parent,
        )
        self._service = service
        self._current = current
        self.name_edit = QLineEdit("" if current is None else current.name)
        self.name_edit.setObjectName("championship-name")
        self.description_edit = QLineEdit("" if current is None else current.description or "")
        self.description_edit.setObjectName("championship-description")
        self.season_edit = QSpinBox()
        self.season_edit.setObjectName("championship-season")
        self.season_edit.setRange(1950, 2100)
        self.season_edit.setValue(date.today().year if current is None else current.season_year)
        self.starts_check = QCheckBox(tr("championship.field.starts"))
        self.starts_check.setObjectName("championship-starts")
        self.starts_edit = _date_edit()
        self.starts_edit.setObjectName("championship-starts-date")
        self.ends_check = QCheckBox(tr("championship.field.ends"))
        self.ends_check.setObjectName("championship-ends")
        self.ends_edit = _date_edit()
        self.ends_edit.setObjectName("championship-ends-date")
        self.teams_box = QCheckBox(tr("championship.field.teams"))
        self.teams_box.setObjectName("championship-teams-enabled")
        if current is not None:
            self.starts_check.setChecked(current.starts_on is not None)
            self.ends_check.setChecked(current.ends_on is not None)
            _show_date(self.starts_edit, current.starts_on)
            _show_date(self.ends_edit, current.ends_on)
            self.teams_box.setChecked(current.teams_enabled)
        self.form.addRow(tr("championship.field.name"), self.name_edit)
        self.form.addRow(tr("championship.field.description"), self.description_edit)
        self.form.addRow(tr("championship.field.season"), self.season_edit)
        self.form.addRow(self.starts_check, self.starts_edit)
        self.form.addRow(self.ends_check, self.ends_edit)
        self.form.addRow("", self.teams_box)

    def submit(self) -> None:
        data = ChampionshipInput(
            name=self.name_edit.text(),
            description=self.description_edit.text(),
            season_year=self.season_edit.value(),
            starts_on=_python_date(self.starts_edit) if self.starts_check.isChecked() else None,
            ends_on=_python_date(self.ends_edit) if self.ends_check.isChecked() else None,
            teams_enabled=self.teams_box.isChecked(),
        )
        if self._current is None:
            self._service.create(data)
        else:
            self._service.update(self._current.id, data)


class RaceResultDialog(QDialog):
    """The stored race report. An open race shows the empty report and no invented result."""

    def __init__(
        self,
        translator: Translator,
        service: ChampionshipService,
        race_id: int,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("championship-race-dialog")
        self.setWindowTitle(translator.translate("championship.result"))
        self.setMinimumSize(880, 560)
        self.report = RaceReportView(translator)
        layout = QVBoxLayout(self)
        layout.addWidget(self.report)
        self.report.show_race(service.completed_race(race_id))


class ChampionshipsPage(QWidget):
    """Overview and one championship: calendar, driver table, optional teams and rules."""

    def __init__(
        self,
        translator: Translator,
        service: ChampionshipService,
        drivers: DriverCatalog,
    ) -> None:
        super().__init__()
        self.setObjectName("championships-page")
        self._translator = translator
        self._service = service
        self._drivers = drivers
        self._rows: tuple[ChampionshipInfo, ...] = ()
        self._board: ChampionshipBoard | None = None
        self._championship_id: int | None = None
        self.dialog_runner: Callable[[QDialog], int] = lambda dialog: dialog.exec()
        self.confirm: Callable[[str], bool] = lambda text: _ask_yes_no(
            self, translator.translate("common.confirm"), text
        )
        self.stack = QStackedWidget()
        self.stack.addWidget(self._build_list())
        self.stack.addWidget(self._build_detail())
        layout = QVBoxLayout(self)
        configure_page(layout)
        layout.addWidget(self.stack)
        self.refresh()

    def showEvent(self, event: QShowEvent) -> None:  # noqa: N802
        super().showEvent(event)
        self.refresh()

    def refresh(self) -> None:
        self._guard(self._reload)

    def add(self) -> None:
        self._guard(lambda: self._edit(None))

    def edit_selected(self) -> None:
        info = self._selected()
        if info is not None and not info.closed:
            self._guard(lambda: self._edit(info))

    def open_selected(self) -> None:
        info = self._selected()
        if info is None:
            return
        self._championship_id = info.id
        self.stack.setCurrentIndex(1)
        self._guard(self._load_detail)

    def activate_selected(self) -> None:
        info = self._selected()
        if info is not None and info.status == PLANNED:
            self._guard(lambda: self._set_status(info.id, ACTIVE))

    def archive_selected(self) -> None:
        info = self._selected()
        if info is None or info.closed:
            return
        self._guard(lambda: self._archive(info))

    def delete_selected(self) -> None:
        info = self._selected()
        if info is not None:
            self._guard(lambda: self._delete(info))

    def show_list(self) -> None:
        self._championship_id = None
        self._board = None
        self.stack.setCurrentIndex(0)
        self._guard(self._load_list)

    def add_race(self) -> None:
        race_id = self.race_choices.currentData()
        if self._championship_id is None or race_id is None:
            return
        championship_id = self._required_id()
        self._guard(lambda: self._change(lambda: self._service.add_race(championship_id, race_id)))

    def remove_selected_race(self) -> None:
        race = self._selected_race()
        if race is None:
            return
        championship_id = self._required_id()
        race_id = race.race_id

        def remove() -> None:
            self._service.remove_race(championship_id, race_id)

        self._guard(lambda: self._change(remove))

    def move_race_up(self) -> None:
        self._move(-1)

    def move_race_down(self) -> None:
        self._move(1)

    def open_selected_race(self) -> None:
        race = self._selected_race()
        if race is None:
            return
        self.dialog_runner(RaceResultDialog(self._translator, self._service, race.race_id, self))

    def save_rules(self) -> None:
        if self._championship_id is None:
            return
        self._guard(self._save_rules)

    def add_point_row(self) -> None:
        row = self.points_table.rowCount()
        place = 1
        if row > 0:
            previous = self.points_table.item(row - 1, 0)
            if previous is not None and previous.text().strip().isdigit():
                place = int(previous.text().strip()) + 1
        self.points_table.insertRow(row)
        self.points_table.setItem(row, 0, QTableWidgetItem(str(place)))
        self.points_table.setItem(row, 1, QTableWidgetItem("0"))

    def remove_point_row(self) -> None:
        if self.points_table.rowCount() > 1:
            self.points_table.removeRow(self.points_table.rowCount() - 1)

    def add_team(self) -> None:
        if self._championship_id is None:
            return
        name = self.team_name.text()
        championship_id = self._required_id()

        def create() -> None:
            self._service.add_team(championship_id, name)

        self._guard(lambda: self._change(create))

    def delete_selected_team(self) -> None:
        team_id = selected_id(self.team_table)
        if self._championship_id is None or team_id is None:
            return
        championship_id = self._required_id()

        def delete_team() -> None:
            self._service.delete_team(championship_id, team_id)

        self._guard(lambda: self._change(delete_team))

    def assign_driver(self) -> None:
        self._assign(self.team_choices.currentData())

    def unassign_driver(self) -> None:
        driver_id = selected_id(self.members_table)
        if driver_id is None:
            return
        self._assign_driver(driver_id, None)

    def refresh_selected_race_teams(self) -> None:
        race = self._selected_race()
        if race is None:
            return
        self._guard(
            lambda: self._change(
                lambda: self._service.refresh_race_teams(self._required_id(), race.race_id)
            )
        )

    def _build_list(self) -> QWidget:
        tr = self._translator.translate
        page = QWidget()
        self.table = make_table(
            [
                tr("championship.column.name"),
                tr("championship.column.season"),
                tr("championship.column.status"),
                tr("championship.column.races"),
            ],
            "championships-table",
        )
        self.empty = QLabel(tr("championship.empty"))
        self.empty.setObjectName("championships-empty")
        self.empty.setWordWrap(True)
        set_role(self.empty, "caption")
        self.status = StatusLabel("championship-status")
        self.add_button = self._button(tr("common.add"), "championships-add", "primary")
        self.edit_button = self._button(tr("common.edit"), "championships-edit", "secondary")
        self.open_button = self._button(tr("championship.open"), "championships-open", "secondary")
        self.activate_button = self._button(
            tr("championship.activate"), "championships-activate", "ghost"
        )
        self.archive_button = self._button(
            tr("championship.archive"), "championships-archive", "ghost"
        )
        self.delete_button = self._button(tr("common.delete"), "championships-delete", "danger")
        buttons = QHBoxLayout()
        buttons.setSpacing(SPACE.sm)
        for button in (
            self.add_button,
            self.edit_button,
            self.open_button,
            self.activate_button,
            self.archive_button,
            self.delete_button,
        ):
            buttons.addWidget(button)
        buttons.addStretch(1)
        layout = QVBoxLayout(page)
        configure_page(layout)
        layout.addLayout(buttons)
        layout.addWidget(self.empty)
        layout.addWidget(self.table, 1)
        layout.addWidget(self.status)
        self.add_button.clicked.connect(self.add)
        self.edit_button.clicked.connect(self.edit_selected)
        self.open_button.clicked.connect(self.open_selected)
        self.activate_button.clicked.connect(self.activate_selected)
        self.archive_button.clicked.connect(self.archive_selected)
        self.delete_button.clicked.connect(self.delete_selected)
        self.table.itemSelectionChanged.connect(self._update_list_buttons)
        self.table.cellDoubleClicked.connect(lambda *_args: self.open_selected())
        return page

    def _build_detail(self) -> QWidget:
        tr = self._translator.translate
        scroll = QScrollArea()
        scroll.setObjectName("championship-detail")
        scroll.setWidgetResizable(True)
        inner = QWidget()
        layout = QVBoxLayout(inner)
        configure_page(layout)
        header = QHBoxLayout()
        self.back_button = self._button(tr("championship.back"), "championship-back", "ghost")
        self.title_label = QLabel()
        self.title_label.setObjectName("championship-title")
        set_role(self.title_label, "page-title")
        self.state_label = QLabel()
        self.state_label.setObjectName("championship-state")
        header.addWidget(self.back_button)
        header.addWidget(self.title_label, 1)
        header.addWidget(self.state_label)
        self.closed_label = QLabel(tr("championship.closed"))
        self.closed_label.setObjectName("championship-closed")
        self.closed_label.setWordWrap(True)
        set_role(self.closed_label, "caption")
        layout.addLayout(header)
        layout.addWidget(self.closed_label)
        calendar = self._section(tr("championship.calendar"), "championship-calendar-heading")
        layout.addWidget(calendar)
        self.races_empty = QLabel(tr("championship.no_races"))
        self.races_empty.setObjectName("championship-races-empty")
        self.races_empty.setWordWrap(True)
        set_role(self.races_empty, "caption")
        self.races_table = make_table(
            [
                tr("championship.column.order"),
                tr("championship.column.race"),
                tr("championship.column.status"),
            ],
            "championship-races",
        )
        self.race_choices = QComboBox()
        self.race_choices.setObjectName("championship-race-choices")
        self.add_race_button = self._button(
            tr("championship.add_race"), "championship-add-race", "primary"
        )
        self.remove_race_button = self._button(
            tr("championship.remove_race"), "championship-remove-race", "danger"
        )
        self.up_button = self._button(tr("championship.up"), "championship-race-up", "ghost")
        self.down_button = self._button(tr("championship.down"), "championship-race-down", "ghost")
        race_actions = QHBoxLayout()
        race_actions.addWidget(self.race_choices, 1)
        for button in (
            self.add_race_button,
            self.remove_race_button,
            self.up_button,
            self.down_button,
        ):
            race_actions.addWidget(button)
        layout.addWidget(self.races_empty)
        layout.addWidget(self.races_table)
        layout.addLayout(race_actions)
        standings = self._section(tr("championship.standings"), "championship-standings-heading")
        layout.addWidget(standings)
        self.standings_empty = QLabel(tr("championship.no_standings"))
        self.standings_empty.setObjectName("championship-standings-empty")
        set_role(self.standings_empty, "caption")
        self.standings_table = make_table([], "championship-standings")
        layout.addWidget(self.standings_empty)
        layout.addWidget(self.standings_table)
        self.teams_group = self._build_teams()
        layout.addWidget(self.teams_group)
        layout.addWidget(self._section(tr("championship.rules"), "championship-rules-heading"))
        self.tie_label = QLabel(tr("championship.tie_break"))
        self.tie_label.setObjectName("championship-tie-break")
        self.tie_label.setWordWrap(True)
        set_role(self.tie_label, "caption")
        self.drop_label = QLabel(tr("championship.drop_note"))
        self.drop_label.setObjectName("championship-drop-note")
        self.drop_label.setWordWrap(True)
        set_role(self.drop_label, "caption")
        self.points_table = make_table(
            [tr("championship.column.place"), tr("championship.column.place_points")],
            "championship-points",
        )
        editable = QAbstractItemView.EditTrigger.DoubleClicked
        editable |= QAbstractItemView.EditTrigger.EditKeyPressed
        self.points_table.setEditTriggers(editable)
        self.bonus_edit = QSpinBox()
        self.bonus_edit.setObjectName("championship-bonus")
        self.bonus_edit.setRange(0, 1000)
        self.drops_edit = QSpinBox()
        self.drops_edit.setObjectName("championship-drops")
        self.drops_edit.setRange(0, 100)
        self.add_place_button = self._button(
            tr("championship.add_place"), "championship-point-add", "ghost"
        )
        self.remove_place_button = self._button(
            tr("championship.remove_place"), "championship-point-remove", "ghost"
        )
        self.save_rules_button = self._button(
            tr("championship.save_rules"), "championship-save-rules", "primary"
        )
        rules = QHBoxLayout()
        rules.addWidget(QLabel(tr("championship.bonus")))
        rules.addWidget(self.bonus_edit)
        rules.addWidget(QLabel(tr("championship.drops")))
        rules.addWidget(self.drops_edit)
        rules.addWidget(self.add_place_button)
        rules.addWidget(self.remove_place_button)
        rules.addWidget(self.save_rules_button)
        rules.addStretch(1)
        layout.addWidget(self.tie_label)
        layout.addWidget(self.drop_label)
        layout.addWidget(self.points_table)
        layout.addLayout(rules)
        layout.addWidget(
            self._section(tr("championship.race_scores"), "championship-race-scores-heading")
        )
        self.race_scores = make_table(
            [
                tr("championship.column.driver"),
                tr("championship.column.place"),
                tr("championship.column.points"),
                tr("championship.column.bonus"),
                tr("championship.column.total"),
                tr("championship.column.dropped"),
                tr("championship.column.fastest"),
                tr("championship.column.note"),
            ],
            "championship-race-scores",
        )
        layout.addWidget(self.race_scores)
        self.detail_status = StatusLabel("championship-detail-status")
        layout.addWidget(self.detail_status)
        scroll.setWidget(inner)
        self.back_button.clicked.connect(self.show_list)
        self.add_race_button.clicked.connect(self.add_race)
        self.remove_race_button.clicked.connect(self.remove_selected_race)
        self.up_button.clicked.connect(self.move_race_up)
        self.down_button.clicked.connect(self.move_race_down)
        self.races_table.cellDoubleClicked.connect(lambda *_args: self.open_selected_race())
        self.races_table.itemSelectionChanged.connect(self._show_race_scores)
        self.add_place_button.clicked.connect(self.add_point_row)
        self.remove_place_button.clicked.connect(self.remove_point_row)
        self.save_rules_button.clicked.connect(self.save_rules)
        return scroll

    def _build_teams(self) -> QWidget:
        tr = self._translator.translate
        group = QWidget()
        group.setObjectName("championship-teams")
        layout = QVBoxLayout(group)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._section(tr("championship.teams"), "championship-teams-heading"))
        self.teams_empty = QLabel(tr("championship.no_teams"))
        self.teams_empty.setObjectName("championship-teams-empty")
        set_role(self.teams_empty, "caption")
        self.team_table = make_table([tr("championship.column.team")], "championship-team-list")
        self.team_name = QLineEdit()
        self.team_name.setObjectName("championship-team-name")
        self.team_name.setPlaceholderText(tr("championship.team_name"))
        self.add_team_button = self._button(
            tr("championship.add_team"), "championship-team-add", "primary"
        )
        self.delete_team_button = self._button(
            tr("championship.delete_team"), "championship-team-delete", "danger"
        )
        team_actions = QHBoxLayout()
        team_actions.addWidget(self.team_name, 1)
        team_actions.addWidget(self.add_team_button)
        team_actions.addWidget(self.delete_team_button)
        self.driver_choices = QComboBox()
        self.driver_choices.setObjectName("championship-driver")
        self.team_choices = QComboBox()
        self.team_choices.setObjectName("championship-team")
        self.assign_button = self._button(
            tr("championship.assign"), "championship-assign", "secondary"
        )
        self.unassign_button = self._button(
            tr("championship.unassign"), "championship-unassign", "ghost"
        )
        assign = QHBoxLayout()
        assign.addWidget(self.driver_choices, 1)
        assign.addWidget(self.team_choices, 1)
        assign.addWidget(self.assign_button)
        assign.addWidget(self.unassign_button)
        self.members_table = make_table(
            [tr("championship.column.driver"), tr("championship.column.team")],
            "championship-members",
        )
        self.refresh_teams_button = self._button(
            tr("championship.refresh_teams"), "championship-refresh-teams", "ghost"
        )
        self.team_standings = make_table([], "championship-team-standings")
        layout.addWidget(self.teams_empty)
        layout.addWidget(self.team_table)
        layout.addLayout(team_actions)
        layout.addLayout(assign)
        layout.addWidget(self.members_table)
        layout.addWidget(self.refresh_teams_button)
        layout.addWidget(self.team_standings)
        self.add_team_button.clicked.connect(self.add_team)
        self.delete_team_button.clicked.connect(self.delete_selected_team)
        self.assign_button.clicked.connect(self.assign_driver)
        self.unassign_button.clicked.connect(self.unassign_driver)
        self.refresh_teams_button.clicked.connect(self.refresh_selected_race_teams)
        return group

    def _reload(self) -> None:
        if self.stack.currentIndex() == 1 and self._championship_id is not None:
            self._load_detail()
        else:
            self._load_list()

    def _load_list(self) -> None:
        selected = selected_id(self.table)
        self._rows = self._service.list_championships()
        tr = self._translator.translate
        fill_table(
            self.table,
            [
                (
                    row.name,
                    str(row.season_year),
                    tr(f"championship.status.{row.status}"),
                    str(row.race_count),
                )
                for row in self._rows
            ],
            [row.id for row in self._rows],
            keep_selection=False,
        )
        if selected is not None:
            self._select(self.table, selected)
        has_rows = bool(self._rows)
        self.empty.setVisible(not has_rows)
        self.table.setVisible(has_rows)
        self._update_list_buttons()

    def _load_detail(self) -> None:
        championship_id = self._required_id()
        try:
            board = self._service.board(championship_id)
        except ValidationError:
            self._championship_id = None
            self.stack.setCurrentIndex(0)
            raise
        self._board = board
        info = board.info
        tr = self._translator.translate
        self.title_label.setText(f"{info.name} {info.season_year}")
        self.state_label.setText(tr(f"championship.status.{info.status}"))
        self.closed_label.setVisible(info.closed)
        self._fill_races(board)
        self._fill_standings(board)
        self._fill_rules(board)
        self._fill_teams(board)
        self._apply_closed(info.closed)
        self._show_race_scores()

    def _fill_races(self, board: ChampionshipBoard) -> None:
        tr = self._translator.translate
        selected = selected_id(self.races_table)
        fill_table(
            self.races_table,
            [
                (str(race.sort_order), race.name, tr(f"championship.race.{race.status.value}"))
                for race in board.races
            ],
            [race.race_id for race in board.races],
            keep_selection=False,
        )
        if selected is not None:
            self._select(self.races_table, selected)
        self.races_empty.setVisible(not board.races)
        self.races_table.setVisible(bool(board.races))
        self.race_choices.blockSignals(True)
        self.race_choices.clear()
        for entry in board.available:
            self.race_choices.addItem(entry.name, entry.race_id)
        self.race_choices.blockSignals(False)

    def _fill_standings(self, board: ChampionshipBoard) -> None:
        tr = self._translator.translate
        headers = [
            tr("championship.column.place"),
            tr("championship.column.driver"),
            tr("championship.column.points"),
            tr("championship.column.counted"),
            tr("championship.column.wins"),
            tr("championship.column.podiums"),
            tr("championship.column.bonus"),
            tr("championship.column.gap"),
        ]
        headers.extend(race.name for race in board.races)
        self._set_headers(self.standings_table, headers)
        rows = [
            (
                str(row.rank),
                row.driver_label,
                str(row.points),
                str(row.counted_races),
                str(row.wins),
                str(row.podiums),
                str(row.bonus_points),
                str(row.gap),
                *(_score_cell(result) for result in row.results),
            )
            for row in board.standings
        ]
        fill_sortable(self.standings_table, rows, [row.driver_id for row in board.standings])
        self.standings_empty.setVisible(not board.standings)
        self.standings_table.setVisible(bool(board.standings))

    def _fill_rules(self, board: ChampionshipBoard) -> None:
        self.points_table.setRowCount(len(board.points))
        for row, (place, points) in enumerate(board.points):
            self.points_table.setItem(row, 0, QTableWidgetItem(str(place)))
            self.points_table.setItem(row, 1, QTableWidgetItem(str(points)))
        self.bonus_edit.setValue(board.info.fastest_lap_bonus)
        self.drops_edit.setValue(board.info.drop_count)

    def _fill_teams(self, board: ChampionshipBoard) -> None:
        enabled = board.info.teams_enabled
        self.teams_group.setVisible(enabled)
        if not enabled:
            return
        tr = self._translator.translate
        self.teams_empty.setVisible(not board.team_rows)
        fill_table(
            self.team_table,
            [(team.name,) for team in board.team_rows],
            [team.id for team in board.team_rows],
        )
        self._fill_choices(
            self.driver_choices,
            [(driver.label, int(driver.id)) for driver in self._drivers.list_drivers()],
        )
        self._fill_choices(self.team_choices, [(team.name, team.id) for team in board.team_rows])
        fill_table(
            self.members_table,
            [(member.driver_label, member.team_name) for member in board.members],
            [member.driver_id for member in board.members],
        )
        headers = [
            tr("championship.column.place"),
            tr("championship.column.team"),
            tr("championship.column.points"),
            tr("championship.column.results"),
            tr("championship.column.drivers"),
            tr("championship.column.wins"),
            tr("championship.column.gap"),
        ]
        self._set_headers(self.team_standings, headers)
        fill_sortable(
            self.team_standings,
            [
                (
                    str(row.rank),
                    row.name,
                    str(row.points),
                    str(row.counted_results),
                    str(row.drivers),
                    str(row.wins),
                    str(row.gap),
                )
                for row in board.teams
            ],
            [row.team_id for row in board.teams],
        )

    def _show_race_scores(self) -> None:
        board = self._board
        race = self._selected_race()
        rows: list[tuple[str, ...]] = []
        ids: list[int] = []
        if board is not None and race is not None:
            found = [
                (driver, result)
                for driver in board.standings
                for result in driver.results
                if result.race_id == race.race_id
            ]
            found.sort(key=lambda item: (item[1].position is None, item[1].position or 0))
            tr = self._translator.translate
            for driver, result in found:
                rows.append(
                    (
                        driver.driver_label,
                        _place_text(result),
                        str(result.place_points),
                        str(result.bonus_points),
                        str(result.total),
                        tr("championship.yes" if result.dropped else "championship.no"),
                        tr("championship.yes" if result.fastest else "championship.no"),
                        _note(tr, result),
                    )
                )
                ids.append(driver.driver_id)
        fill_table(self.race_scores, rows, ids, keep_selection=False)
        closed = board is not None and board.info.closed
        finished = race is not None and race.status is RaceStatus.FINISHED
        self.refresh_teams_button.setEnabled(finished and not closed)
        self.remove_race_button.setEnabled(race is not None and not closed)
        self.up_button.setEnabled(race is not None and not closed)
        self.down_button.setEnabled(race is not None and not closed)

    def _apply_closed(self, closed: bool) -> None:
        for widget in (
            self.race_choices,
            self.add_race_button,
            self.points_table,
            self.bonus_edit,
            self.drops_edit,
            self.add_place_button,
            self.remove_place_button,
            self.save_rules_button,
            self.team_name,
            self.add_team_button,
            self.delete_team_button,
            self.driver_choices,
            self.team_choices,
            self.assign_button,
            self.unassign_button,
        ):
            widget.setEnabled(not closed)
        self.add_race_button.setEnabled(not closed and self.race_choices.count() > 0)

    def _save_rules(self) -> None:
        points: list[tuple[int, int]] = []
        for row in range(self.points_table.rowCount()):
            place = _int_cell(self.points_table, row, 0)
            value = _int_cell(self.points_table, row, 1)
            points.append((place, value))
        self._service.save_points(
            self._required_id(),
            tuple(points),
            fastest_lap_bonus=self.bonus_edit.value(),
            drop_count=self.drops_edit.value(),
        )
        self._load_detail()
        self.detail_status.show_info(self._translator.translate("common.saved"))

    def _edit(self, current: ChampionshipInfo | None) -> None:
        dialog = ChampionshipDialog(self._translator, self._service, current, self)
        if self.dialog_runner(dialog) == QDialog.DialogCode.Accepted:
            self._load_list()
            self.status.show_info(self._translator.translate("common.saved"))

    def _archive(self, info: ChampionshipInfo) -> None:
        question = self._translator.format("championship.confirm_archive", name=info.name)
        if not self.confirm(question):
            return
        self._service.set_status(info.id, COMPLETED)
        self._load_list()
        self.status.show_info(self._translator.translate("common.saved"))

    def _set_status(self, championship_id: int, status: str) -> None:
        self._service.set_status(championship_id, status)
        self._load_list()
        self.status.show_info(self._translator.translate("common.saved"))

    def _delete(self, info: ChampionshipInfo) -> None:
        question = self._translator.format("common.confirm_delete", name=info.name)
        if not self.confirm(question):
            return
        self._service.delete(info.id)
        self._load_list()
        self.status.show_info(self._translator.translate("common.deleted"))

    def _change(self, action: Callable[[], None]) -> None:
        action()
        self.team_name.clear()
        self._load_detail()
        self.detail_status.show_info(self._translator.translate("common.saved"))

    def _move(self, delta: int) -> None:
        race = self._selected_race()
        if race is None:
            return
        self._guard(
            lambda: self._change(
                lambda: self._service.move_race(self._required_id(), race.race_id, delta)
            )
        )

    def _assign(self, team_id: object) -> None:
        driver_id = self.driver_choices.currentData()
        if not isinstance(driver_id, int) or not isinstance(team_id, int):
            return
        self._assign_driver(driver_id, team_id)

    def _assign_driver(self, driver_id: int, team_id: int | None) -> None:
        self._guard(
            lambda: self._change(
                lambda: self._service.assign_driver(self._required_id(), driver_id, team_id)
            )
        )

    def _selected(self) -> ChampionshipInfo | None:
        championship_id = selected_id(self.table)
        return next((row for row in self._rows if row.id == championship_id), None)

    def _selected_race(self) -> LinkedRace | None:
        race_id = selected_id(self.races_table)
        if self._board is None or race_id is None:
            return None
        return next((race for race in self._board.races if race.race_id == race_id), None)

    def _required_id(self) -> int:
        if self._championship_id is None:
            raise RuntimeError("no championship is open")
        return self._championship_id

    def _update_list_buttons(self) -> None:
        info = self._selected()
        has = info is not None
        self.edit_button.setEnabled(has and info is not None and not info.closed)
        self.open_button.setEnabled(has)
        self.delete_button.setEnabled(has)
        self.activate_button.setEnabled(info is not None and info.status == PLANNED)
        self.archive_button.setEnabled(info is not None and not info.closed)

    def _guard(self, action: Callable[[], None]) -> None:
        try:
            action()
        except Exception as error:
            if not is_expected(error):
                logger.exception("Action failed on the championship page")
            message = describe_error(self._translator, error)
            target = self.detail_status if self.stack.currentIndex() == 1 else self.status
            target.show_error(message)

    def _button(self, text: str, object_name: str, role: str) -> QPushButton:
        button = QPushButton(text)
        button.setObjectName(object_name)
        set_role(button, role)
        return button

    def _section(self, text: str, object_name: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName(object_name)
        set_role(label, "section")
        return label

    @staticmethod
    def _set_headers(table: QTableWidget, headers: list[str]) -> None:
        table.setColumnCount(len(headers))
        table.setHorizontalHeaderLabels(headers)

    @staticmethod
    def _select(table: QTableWidget, entity_id: int) -> None:
        for row in range(table.rowCount()):
            item = table.item(row, 0)
            if item is not None and item.data(Qt.ItemDataRole.UserRole) == entity_id:
                table.selectRow(row)
                return

    @staticmethod
    def _fill_choices(combo: QComboBox, rows: list[tuple[str, int]]) -> None:
        selected = combo.currentData()
        combo.blockSignals(True)
        combo.clear()
        for label, value in rows:
            combo.addItem(label, value)
        if selected is not None:
            index = combo.findData(selected)
            if index >= 0:
                combo.setCurrentIndex(index)
        combo.blockSignals(False)


def _score_cell(result: RaceScore) -> str:
    if result.disqualified:
        return "DQ"
    if not result.classified:
        return MISSING
    text = str(result.total)
    if result.dropped:
        return f"{text}*"
    return text


def _place_text(result: RaceScore) -> str:
    if result.disqualified:
        return "DQ"
    if result.position is None:
        return MISSING
    return str(result.position)


def _note(translate: Callable[[str], str], result: RaceScore) -> str:
    notes: list[str] = []
    if result.absent:
        notes.append(translate("championship.note.absent"))
    if result.disqualified:
        notes.append(translate("championship.note.disqualified"))
    if result.dropped:
        notes.append(translate("championship.note.dropped"))
    if result.merged:
        notes.append(translate("championship.note.merged"))
    return ", ".join(notes)


def _int_cell(table: QTableWidget, row: int, column: int) -> int:
    item = table.item(row, column)
    text = "" if item is None else item.text().strip()
    if not text.isdigit():
        raise ValidationError("error.championship.points")
    return int(text)
