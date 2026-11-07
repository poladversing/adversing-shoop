"""Dashboard administrator avatar presets stay separate from the user gallery."""
import io
import pathlib
import secrets

from PIL import Image


ORIGIN = {"Origin": "http://testserver"}

EXPECTED_STEMS = (
    "16_TiTaN_Mountain_Wordmark",
    "06_TiTaN_Mountain_Cube",
    "10_TiTaN_Crystal_Cube",
    "11_TiTaN_Nebula_Cube",
    "12_TiTaN_Crown_Emblem",
    "13_TiTaN_Digital_Wave",
)
DEFAULT_KEY = "dashboard:16_TiTaN_Mountain_Wordmark"


def test_dashboard_avatar_catalog_has_six_only_and_wordmark_is_first(admin):
    response = admin.get("/api/dashboard-avatars")
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["default"] == DEFAULT_KEY
    assert [item["id"] for item in data["items"]] == [
        f"dashboard:{stem}" for stem in EXPECTED_STEMS
    ]
    assert all(item["builtin"] for item in data["items"])
    for stem, item in zip(EXPECTED_STEMS, data["items"], strict=True):
        assert item["url"] == f"/static/img/dashboard-profiles/{stem}.png"
        image = admin.get(item["url"])
        assert image.status_code == 200
        assert image.headers["content-type"].startswith("image/png")
        assert len(image.content) > 80_000


def test_fresh_dashboard_default_and_server_render_use_wordmark(admin, db, monkeypatch):
    from app import config
    from app.main import _resolve_dashboard_avatar

    assert config.DEFAULT_SETTINGS["admin_avatar"] == DEFAULT_KEY
    default = _resolve_dashboard_avatar("titan")
    assert default == {
        "key": DEFAULT_KEY,
        "url": "/static/img/dashboard-profiles/16_TiTaN_Mountain_Wordmark.png",
    }
    monkeypatch.setattr(db, "get_settings", lambda: dict(config.DEFAULT_SETTINGS))
    response = admin.get("/dashboard")
    assert response.status_code == 200, response.text
    assert response.text.count(
        'src="/static/img/dashboard-profiles/16_TiTaN_Mountain_Wordmark.png"'
    ) == 2  # header profile and settings preview; the sidebar badge is branding


def test_sidebar_version_badge_is_branding_not_the_admin_avatar(admin):
    from app import APP_VERSION

    response = admin.get("/dashboard")
    assert response.status_code == 200, response.text
    assert '<div class="version-logo"><img src="/static/img/titan-cube.svg" alt="" aria-hidden="true"></div>' in response.text
    assert f'<div class="version-v">v{APP_VERSION}</div>' in response.text
    assert '<div class="online"><span class="dot" aria-hidden="true"></span><span>آنلاین</span></div>' in response.text
    image = admin.get("/static/img/titan-cube.svg")
    assert image.status_code == 200 and "<svg" in image.text

    bridge = (pathlib.Path(__file__).parents[1] / "static" / "js" / "titan-bridge.js").read_text(encoding="utf-8")
    assert "const vvAv=$('.version-logo img')" not in bridge
    assert "const verLogo=document.querySelector('.version-logo')" not in bridge
    assert "if(ver) ver.onclick=" not in bridge


def test_admin_avatar_selection_is_isolated_from_user_profile_gallery(admin, make_user, db):
    previous_avatar = db.get_settings().get("admin_avatar")
    user = make_user(avatar="gallery:g3")
    chosen_key = "dashboard:06_TiTaN_Mountain_Cube"
    try:
        saved = admin.post("/api/admin-avatar", json={"avatar": chosen_key}, headers=ORIGIN)
        assert saved.status_code == 200, saved.text
        assert saved.json()["avatar"] == {
            "key": chosen_key,
            "url": "/static/img/dashboard-profiles/06_TiTaN_Mountain_Cube.png",
        }

        me = admin.get("/api/me").json()
        assert me["avatar"]["key"] == chosen_key
        assert me["avatar"]["url"].endswith("/static/img/dashboard-profiles/06_TiTaN_Mountain_Cube.png")
        users = admin.get("/api/users").json()["users"]
        current = next(item for item in users if item["uid"] == user["uid"])
        from app.main import LEGACY_GALLERY_AVATAR_REMAP

        expected = f"gallery:{LEGACY_GALLERY_AVATAR_REMAP['g3']}"
        assert current["avatar"] == expected
        assert current["avatar_url"] == f"/static/img/user-profiles/{LEGACY_GALLERY_AVATAR_REMAP['g3']}.png"
    finally:
        db.set_setting("admin_avatar", previous_avatar or DEFAULT_KEY)


def test_user_gallery_contains_only_five_user_png_presets(admin):
    from app import config

    response = admin.get("/api/gallery")
    assert response.status_code == 200, response.text
    items = {item["id"]: item for item in response.json()["items"] if item["builtin"]}
    assert set(items) == {f"gallery:{stem}" for stem in config.USER_PROFILE_AVATAR_STEMS}
    for stem in config.USER_PROFILE_AVATAR_STEMS:
        item = items[f"gallery:{stem}"]
        assert item["url"] == f"/static/img/user-profiles/{stem}.png"
        image = admin.get(item["url"])
        assert image.status_code == 200
        assert image.headers["content-type"].startswith("image/png")
    assert len(items) == len(config.USER_PROFILE_AVATAR_STEMS) == 5
    assert all(not item_id.startswith("dashboard:") for item_id in items)
    assert not any(item_id in {f"gallery:g{i}" for i in range(1, 7)} for item_id in items)
    gallery_dir = pathlib.Path(__file__).parents[1] / "static" / "img" / "gallery"
    assert not list(gallery_dir.glob("g[1-6].svg"))


def test_admin_avatar_endpoint_rejects_user_gallery_keys(admin):
    from app import config

    before = admin.get("/api/me").json()["avatar"]
    for key in ("gallery:g1", f"gallery:{config.USER_PROFILE_AVATAR_STEMS[0]}"):
        rejected = admin.post("/api/admin-avatar", json={"avatar": key}, headers=ORIGIN)
        assert rejected.status_code == 400
        assert admin.get("/api/me").json()["avatar"] == before


def test_custom_dashboard_image_is_persistent_and_separate_from_user_gallery(admin, db):
    from app import config

    previous_avatar = db.get_settings().get("admin_avatar")
    buffer = io.BytesIO()
    Image.new("RGB", (80, 60), (28, 66, 112)).save(buffer, format="PNG")
    response = admin.post(
        "/api/dashboard-avatar-upload",
        files={"file": ("from-device.png", buffer.getvalue(), "image/png")},
        headers=ORIGIN,
    )
    assert response.status_code == 200, response.text
    item = response.json()["item"]
    assert item["id"].startswith("dashboard-upload:")
    assert item["url"].startswith("/api/dashboard-avatar-image/")
    fname = item["id"].split(":", 1)[1]
    stored = pathlib.Path(config.DATA_DIR) / "dashboard-avatars" / fname
    assert stored.is_file() and stored.stat().st_size > 0

    served = admin.get(item["url"])
    assert served.status_code == 200
    assert served.headers["content-type"].startswith("image/png")
    assert served.content.startswith(b"\x89PNG\r\n\x1a\n")

    saved = admin.post("/api/admin-avatar", json={"avatar": item["id"]}, headers=ORIGIN)
    assert saved.status_code == 200, saved.text
    assert saved.json()["avatar"] == {"key": item["id"], "url": item["url"]}
    catalog = admin.get("/api/dashboard-avatars").json()
    assert catalog["current"] == item["id"]
    assert len(catalog["items"]) == 6
    gallery_ids = {row["id"] for row in admin.get("/api/gallery").json()["items"]}
    assert item["id"] not in gallery_ids
    assert not any(row.startswith("dashboard:") for row in gallery_ids)
    db.set_setting("admin_avatar", previous_avatar or DEFAULT_KEY)


def test_legacy_gallery_keys_are_migrated_to_user_pngs(admin, db, make_user):
    from app import config
    from app.main import LEGACY_GALLERY_AVATAR_REMAP, _avatar_path, _migrate_legacy_gallery_avatars

    user = make_user(avatar=f"gallery:{config.USER_PROFILE_AVATAR_STEMS[0]}")
    previous_avatar = db.get_settings().get("admin_avatar")
    db.update_user(user["uid"], {"avatar": "gallery:g1"})
    sub = db.create_subscription(
        "legacy-avatar-link", secrets.token_hex(16),
        [{"uid": user["uid"], "configs": []}], avatar="gallery:g4",
    )
    db.set_setting("admin_avatar", "gallery:g6")
    try:
        assert _migrate_legacy_gallery_avatars() == 3
        assert db.get_user(user["uid"])["avatar"] == f"gallery:{LEGACY_GALLERY_AVATAR_REMAP['g1']}"
        assert db.get_subscription(sub["id"])["avatar"] == f"gallery:{LEGACY_GALLERY_AVATAR_REMAP['g4']}"
        assert db.get_settings()["admin_avatar"] == DEFAULT_KEY
        assert _avatar_path("gallery:g6").endswith(
            f"/static/img/user-profiles/{LEGACY_GALLERY_AVATAR_REMAP['g6']}.png"
        )
        assert _migrate_legacy_gallery_avatars() == 0
    finally:
        db.delete_subscription(sub["id"])
        db.set_setting("admin_avatar", previous_avatar or DEFAULT_KEY)
