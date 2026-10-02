from __future__ import annotations

import pytest

from slot_racing.core.errors import ValidationError
from slot_racing.modules.tracks.service import MAX_LANES, TrackInput
from tests.modules.conftest import Env


def test_create_and_edit_track(env: Env) -> None:
    track = env.tracks.create_track(
        TrackInput(name=" Monza ", lane_count=4, description="Schnell", image_path="monza.png")
    )
    assert (track.name, track.lane_count, track.description) == ("Monza", 4, "Schnell")
    assert track.image_path == "monza.png"
    assert track.is_active
    updated = env.tracks.update_track(track.id, TrackInput(name="Monza 2", lane_count=2))
    assert updated.name == "Monza 2"
    assert updated.description is None
    assert env.tracks.list_tracks() == [updated]


@pytest.mark.parametrize("lanes", [0, -1, MAX_LANES + 1])
def test_lane_count_is_validated(env: Env, lanes: int) -> None:
    with pytest.raises(ValidationError) as caught:
        env.tracks.create_track(TrackInput(name="X", lane_count=lanes))
    assert caught.value.key == "error.track.lane_count"
    assert env.tracks.list_tracks() == []


def test_track_name_is_required(env: Env) -> None:
    with pytest.raises(ValidationError) as caught:
        env.tracks.create_track(TrackInput(name="  "))
    assert caught.value.key == "error.track.name.required"


def test_deactivate_and_delete_track(env: Env) -> None:
    track = env.track()
    env.tracks.set_active(track.id, False)
    assert env.tracks.list_tracks(active_only=True) == []
    env.tracks.delete_track(track.id)
    assert env.tracks.get_track(track.id) is None


def test_track_used_by_a_race_cannot_be_deleted(env: Env) -> None:
    track_id = env.track_id()
    env.races.create_race("R", track_id, 3)
    with pytest.raises(ValidationError) as caught:
        env.tracks.delete_track(track_id)
    assert caught.value.key == "error.track.in_use"
    assert env.tracks.get_track(track_id) is not None
