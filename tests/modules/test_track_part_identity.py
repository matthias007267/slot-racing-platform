"""Identity of a track part is its designation plus its article number."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from slot_racing.app.runtime import Runtime
from slot_racing.core.config import AppConfig
from slot_racing.core.domain import TrackId
from slot_racing.core.errors import ValidationError
from slot_racing.core.storage import Database
from slot_racing.modules.track_planner.parts import (
    CURVE,
    STRAIGHT,
    PartSpec,
    build_part,
    identity_key,
    normalize_article,
    normalize_name,
)
from slot_racing.modules.track_planner.service import TrackPlannerService
from tests.modules.conftest import Env
from tests.modules.test_track_parts import _planner


def test_normalisation_folds_case_and_spaces_and_keeps_leading_zeros() -> None:
    assert normalize_name(" Standardgerade ") == normalize_name("STANDARDGERADE")
    assert normalize_name("standardgerade") == "standardgerade"
    assert normalize_article(" 0200 ") == "0200"
    assert normalize_article("0200") != normalize_article("200")
    assert identity_key(" Standardgerade ", " 20020601 ") == ("standardgerade", "20020601")


def test_duplicates_follow_the_combined_identity(env: Env) -> None:
    planner = _planner(env)
    planner.add_part(_named(name="Standardgerade", article="EB-1", scale="1:32"))
    with pytest.raises(ValidationError) as same:
        planner.add_part(_named(name="Standardgerade", article="EB-1", scale="1:43"))
    assert same.value.key == "error.planner.part_exists"
    with pytest.raises(ValidationError) as folded:
        planner.add_part(_named(name="  STANDARDGERADE ", article=" EB-1 ", scale="1:24"))
    assert folded.value.key == "error.planner.part_exists"
    message = env.runtime.translator.format(folded.value.key, **folded.value.params)
    assert message == (
        "Ein Bauteil mit der Bezeichnung „STANDARDGERADE“ und der Artikelnummer "
        "„EB-1“ existiert bereits."
    )
    same_name = planner.add_part(_named(name="Standardgerade", article="EB-2", scale="1:32"))
    same_article = planner.add_part(_named(name="Andere Gerade", article="EB-1", scale="1:32"))
    assert same_name.id != same_article.id
    zeros = planner.add_part(_named(name="Nullen", article=" 0200 ", scale="1:24"))
    assert zeros.spec.article_number == "0200"
    other_number = planner.add_part(_named(name="Nullen", article="200", scale="1:24"))
    assert other_number.id != zeros.id
    assert other_number.spec.article_number == "200"


def test_editing_keeps_the_part_and_rejects_another_identity(env: Env) -> None:
    planner = _planner(env)
    planner.list_parts()
    created = planner.add_part(_named(name="Eigen", article="EB-9", scale="1:32"))
    unchanged = planner.update_part(created.id, created.spec)
    assert unchanged.id == created.id
    rescaled = planner.update_part(created.id, replace(created.spec, scale="1:43"))
    assert rescaled.id == created.id
    assert rescaled.spec.scale == "1:43"
    assert identity_key(rescaled.spec.name, rescaled.spec.article_number) == ("eigen", "EB-9")
    recategorised = planner.update_part(
        created.id,
        replace(rescaled.spec, category=CURVE, radius_mm=300.0, angle_deg=30.0, length_mm=None),
    )
    assert recategorised.id == created.id
    assert recategorised.spec.category == CURVE
    with pytest.raises(ValidationError) as clash:
        planner.update_part(
            created.id,
            replace(recategorised.spec, name="Standardgerade", article_number="20020601"),
        )
    assert clash.value.key == "error.planner.part_exists"
    kept = planner.library.require(created.id).spec
    assert kept.name == "Eigen"
    assert kept.article_number == "EB-9"
    assert kept.scale == "1:43"


def test_the_database_rejects_only_the_combined_identity(env: Env) -> None:
    with env.runtime.database.engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO track_part_definitions "
                "(article_number, scale, name, category, lane_count, outline) "
                "VALUES ('111', '1:24', 'Alpha', 'straight', 2, '[]')"
            )
        )
        connection.execute(
            text(
                "INSERT INTO track_part_definitions "
                "(article_number, scale, name, category, lane_count, outline) "
                "VALUES ('111', '1:32', 'Beta', 'straight', 2, '[]')"
            )
        )
        connection.execute(
            text(
                "INSERT INTO track_part_definitions "
                "(article_number, scale, name, category, lane_count, outline) "
                "VALUES ('222', '1:24', 'Alpha', 'curve', 2, '[]')"
            )
        )
    with (
        pytest.raises(IntegrityError),
        env.runtime.database.engine.begin() as connection,
    ):
        connection.execute(
            text(
                "INSERT INTO track_part_definitions "
                "(article_number, scale, name, category, lane_count, outline) "
                "VALUES (' 111 ', '1:43', ' alpha ', 'straight', 2, '[]')"
            )
        )


def test_old_rows_merge_on_identity_and_evolution_scale_becomes_one_to_twenty_four(
    tmp_path: Path,
) -> None:
    path = tmp_path / "slot_racing.db"
    database = Database.from_path(path)
    database.migrate("0011")
    with database.engine.begin() as connection:
        connection.exec_driver_sql(
            "INSERT INTO tracks (name, lane_count, is_active) VALUES ('Oval', 2, 1)"
        )
        for values in (
            (1, "Carrera Digital 132", "20020601", "1:32", "Standardgerade", None),
            (2, "Carrera Evolution", "20020601", "1:32", "Standardgerade", None),
            (3, "Carrera GO!!!", " 20020601 ", "1:43", " standardgerade ", None),
            (4, "Eigenbau", "20020601", "1:43", "Andere Gerade", None),
            (5, "Eigenbau", "999", "1:32", "Standardgerade", None),
            (6, "Carrera Digital 132", "20020517", "1:32", "Weiche", '[{"kind": "slot"}]'),
            (7, "Carrera Evolution", "20020517", "1:32", "Weiche", None),
            (8, "Eigenbau", " 0200 ", "1:32", "Nullen", None),
        ):
            connection.exec_driver_sql(
                "INSERT INTO track_part_definitions "
                "(id, system, article_number, scale, name, category, lane_count, outline, "
                "slot_paths) VALUES (?, ?, ?, ?, ?, 'straight', 2, '[]', ?)",
                values,
            )
        placements = (("keep-a", 1, 0), ("keep-b", 2, 345), ("keep-c", 3, 690))
        for instance_id, part_id, x_mm in placements:
            connection.exec_driver_sql(
                "INSERT INTO track_plan_instances "
                "(id, track_id, part_id, x_mm, y_mm, z_mm, rotation_x_deg, rotation_y_deg, "
                "rotation_z_deg, is_start_straight) "
                "VALUES (?, 1, ?, ?, 0, 0, 0, 0, 0, 0)",
                (instance_id, part_id, x_mm),
            )
    database.dispose()

    reopened = Database.from_path(path)
    reopened.migrate()
    with reopened.engine.connect() as connection:
        columns = {
            row[1]
            for row in connection.exec_driver_sql("PRAGMA table_info(track_part_definitions)")
        }
        assert "system" not in columns
        assert "set_name" not in columns
        straights = connection.exec_driver_sql(
            "SELECT id, scale, name, article_number FROM track_part_definitions "
            "WHERE trim(article_number) = '20020601'"
        ).fetchall()
        names = {row[2] for row in straights}
        assert names == {"Standardgerade", "Andere Gerade"}
        standard = next(row for row in straights if row[2] == "Standardgerade")
        assert standard[1] == "1:24"
        pointed = {
            row[0]
            for row in connection.exec_driver_sql(
                "SELECT part_id FROM track_plan_instances ORDER BY id"
            )
        }
        assert pointed == {standard[0]}
        switch = connection.exec_driver_sql(
            "SELECT id, scale, slot_paths FROM track_part_definitions WHERE name = 'Weiche'"
        ).fetchall()
        assert len(switch) == 1
        assert switch[0][0] == 6
        assert switch[0][1] == "1:24"
        assert switch[0][2] not in (None, "", "[]", "null")
        other = connection.exec_driver_sql(
            "SELECT scale, article_number FROM track_part_definitions "
            "WHERE name = 'Standardgerade' AND article_number = '999'"
        ).one()
        assert other == ("1:32", "999")
        zeros = connection.exec_driver_sql(
            "SELECT article_number FROM track_part_definitions WHERE name = 'Nullen'"
        ).scalar_one()
        assert zeros == "0200"
    reopened.dispose()

    runtime = Runtime.create(
        AppConfig(database_path=path, language="de"),
        config_path=tmp_path / "config.json",
    )
    try:
        planner = runtime.services.get(TrackPlannerService)
        loaded = planner.load(TrackId(1))
        assert len(loaded.instances) == 3
        assert len({instance.part_id for instance in loaded.instances}) == 1
        part = planner.library.require(loaded.instances[0].part_id).spec
        assert part.name == "Standardgerade"
        assert part.article_number == "20020601"
        assert part.scale == "1:24"
        assert part.category == STRAIGHT
        again = [
            record
            for record in planner.list_parts()
            if identity_key(record.spec.name, record.spec.article_number)
            == ("standardgerade", "20020601")
        ]
        assert len(again) == 1
    finally:
        runtime.shutdown()


def _named(*, name: str, article: str, scale: str) -> PartSpec:
    return build_part(
        article_number=article,
        scale=scale,
        name=name,
        category=STRAIGHT,
        length_mm=200,
        width_mm=None,
        height_mm=None,
        radius_mm=None,
        angle_deg=None,
        lane_count=2,
    )
