"""User avatar presets and random defaults, independent from dashboard avatars."""

import pathlib
import uuid

import pytest

ORIGIN = {"Origin": "http://testserver"}


def test_random_user_avatar_pool_is_exactly_the_five_supplied_pngs(client, monkeypatch):
    from app import config, main

    seen = []

    def choose(values):
        seen.append(tuple(values))
        return values[-1]

    monkeypatch.setattr(main.secrets, "choice", choose)
    assert main._random_user_avatar_key() == f"gallery:{config.USER_PROFILE_AVATAR_STEMS[-1]}"
    assert seen == [config.USER_PROFILE_AVATAR_STEMS]


def test_new_user_without_choice_gets_a_persisted_random_png(admin, db, make_user, monkeypatch):
    from app import config, main

    stem = config.USER_PROFILE_AVATAR_STEMS[2]
    monkeypatch.setattr(main, "_random_user_avatar_key", lambda: f"gallery:{stem}")
    user = make_user()

    assert user["avatar"] == f"gallery:{stem}"
    assert user["avatar_url"] == f"/static/img/user-profiles/{stem}.png"
    assert db.get_user(user["uid"])["avatar"] == f"gallery:{stem}"
    again = admin.get(f"/api/users/{user['uid']}").json()
    assert again["avatar"] == f"gallery:{stem}"
    assert again["avatar_url"] == user["avatar_url"]


def test_explicit_user_choice_wins_and_dashboard_keys_are_not_user_profiles(
    admin, make_user, monkeypatch
):
    from app import config, main

    monkeypatch.setattr(main, "_random_user_avatar_key", lambda: pytest.fail("random default was used"))
    stem = config.USER_PROFILE_AVATAR_STEMS[1]
    user = make_user(avatar=f"gallery:{stem}")
    assert user["avatar"] == f"gallery:{stem}"
    assert user["avatar_url"] == f"/static/img/user-profiles/{stem}.png"

    rejected = admin.post(
        "/api/users",
        json={"name": "invalid-avatar-" + uuid.uuid4().hex[:8], "avatar": "dashboard:16_TiTaN_Mountain_Wordmark"},
        headers=ORIGIN,
    )
    assert rejected.status_code == 400
    assert rejected.json()["detail"] == "invalid-avatar"


def test_titan_logo_remains_an_explicit_choice(admin, make_user, monkeypatch):
    from app import main

    monkeypatch.setattr(main, "_random_user_avatar_key", lambda: pytest.fail("random default was used"))
    user = make_user(avatar="titan")
    assert user["avatar"] == ""
    assert user["avatar_url"] == "/static/img/titan-avatar.svg"


def test_new_user_png_is_served_by_the_public_subscription_avatar_route(admin, db, make_user):
    from app import config

    stem = config.USER_PROFILE_AVATAR_STEMS[0]
    user = make_user(avatar=f"gallery:{stem}")
    created = admin.post(
        "/api/subscriptions",
        json={"name": "avatar-" + uuid.uuid4().hex[:8], "items": [{"uid": user["uid"], "configs": []}]},
        headers=ORIGIN,
    )
    assert created.status_code == 200, created.text
    subscription = created.json()["subscription"]
    try:
        response = admin.get(f"/s/{subscription['token']}/avatar")
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("image/png")
        image_path = pathlib.Path(__file__).resolve().parent.parent / "static" / "img" / "user-profiles" / f"{stem}.png"
        assert response.content == image_path.read_bytes()
    finally:
        db.delete_subscription(subscription["id"])
