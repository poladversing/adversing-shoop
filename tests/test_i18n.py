"""Focused regression checks for dashboard language and public UI copy."""
from __future__ import annotations

from html.parser import HTMLParser
import json
import os
from pathlib import Path
import re
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parent.parent
LOGIN = (ROOT / "templates" / "login.html").read_text(encoding="utf-8")
DASHBOARD = (ROOT / "templates" / "dashboard.html").read_text(encoding="utf-8")
BRIDGE = (ROOT / "static" / "js" / "titan-bridge.js").read_text(encoding="utf-8")
SUBSCRIPTION = (ROOT / "templates" / "subscription.html").read_text(encoding="utf-8")
PERSIAN = re.compile(r"[\u0600-\u06ff]")


class _UiTextParser(HTMLParser):
    """Collect visible Persian text and translation keys, excluding code/art."""

    _markers = (
        "data-i18n", "data-i18n-ph", "data-i18n-aria", "data-i18n-alt", "data-i18n-title"
    )

    def __init__(self):
        super().__init__()
        self.stack: list[tuple[str, bool, bool]] = []
        self.persian_text: list[str] = []
        self.unannotated_persian: list[str] = []
        self.keys: set[str] = set()
        self.translatable_attributes: list[str] = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        parent_ignored = bool(self.stack and self.stack[-1][1])
        ignored = parent_ignored or tag in {"script", "style", "svg", "head"}
        annotated = any(key in attrs for key in self._markers)
        for key in self._markers:
            if attrs.get(key):
                self.keys.add(attrs[key])
        for key in ("placeholder", "title", "aria-label", "alt", "data-tip", "data-msg", "data-demo"):
            if attrs.get(key) and PERSIAN.search(attrs[key]):
                self.translatable_attributes.append(attrs[key])
        self.stack.append((tag, ignored, annotated or (self.stack[-1][2] if self.stack else False)))

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index][0] == tag:
                del self.stack[index:]
                break

    def handle_data(self, data):
        value = " ".join(data.split())
        if not value or not PERSIAN.search(value) or (self.stack and self.stack[-1][1]):
            return
        self.persian_text.append(value)
        if not (self.stack and self.stack[-1][2]):
            self.unannotated_persian.append(value)


def _parse_ui(text: str) -> _UiTextParser:
    parser = _UiTextParser()
    parser.feed(text)
    return parser


def _subscription_dictionary_keys():
    start = SUBSCRIPTION.index("const I18N={")
    end = SUBSCRIPTION.index("let currentLang", start)
    block = SUBSCRIPTION[start:end]
    fa_start = block.index("fa:{") + len("fa:{")
    en_start = block.index("en:{", fa_start)
    fa_block = block[fa_start:en_start]
    en_block = block[en_start + len("en:{"):]
    key_pattern = re.compile(r"\b([A-Za-z_$][\w$]*):\s*\"")
    return set(key_pattern.findall(fa_block)), set(key_pattern.findall(en_block))


def _dashboard_translate(values: list[str], language: str) -> list[str]:
    if shutil.which("node") is None:
        pytest.skip("node is not installed")
    runner = r"""
const fs = require('fs');
const source = fs.readFileSync(process.env.TITAN_BRIDGE, 'utf8');
const start = source.indexOf('  const DASHBOARD_TEXT=Object.freeze({');
const end = source.indexOf('  function translateDashboard', start);
if (start < 0 || end < 0) throw new Error('dashboard translation block not found');
new Function('globalThis', source.slice(start, end) + '\nglobalThis.__translate = dashboardText;')(globalThis);
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
process.stdout.write(JSON.stringify(input.values.map(v => __translate(v, input.language))));
"""
    result = subprocess.run(
        [shutil.which("node"), "-e", runner],
        input=json.dumps({"values": values, "language": language}, ensure_ascii=False),
        capture_output=True,
        text=True,
        cwd=ROOT,
        env={**os.environ, "TITAN_BRIDGE": str(ROOT / "static" / "js" / "titan-bridge.js")},
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_dashboard_defaults_to_english_and_persists_explicit_preferences():
    assert '<html lang="en" dir="ltr">' in DASHBOARD
    assert 'dashboard-reference.css?v=20261019-dashboard-layout-v9' in DASHBOARD
    assert 'titan-bridge.js?v=20261019-dashboard-language-v5' in DASHBOARD
    assert "let dashboardLang='en';" in BRIDGE
    assert "let saved='en';" in BRIDGE
    assert "dashboardLang=lang==='fa'?'fa':'en';" in BRIDGE
    assert "localStorage.setItem('titan-language',dashboardLang)" in BRIDGE
    assert "localStorage.setItem('titan_lang',dashboardLang)" in BRIDGE
    assert "window._titanReloadNotifications=loadNotifications;" in BRIDGE
    assert "window._titanReloadNotifications?.();" in BRIDGE


def test_login_uses_supplied_design_and_submits_to_existing_auth():
    ui = _parse_ui(LOGIN)
    assert ui.unannotated_persian == []
    assert 'id="loginForm"' in LOGIN
    assert 'name="username" type="text"' in LOGIN
    assert 'name="password" type="password"' in LOGIN
    assert 'id="togglePassword"' in LOGIN and 'id="eyeIcon"' in LOGIN
    assert 'class="bg-art"' in LOGIN and 'class="brand-logo"' in LOGIN
    assert 'const credentials=Object.fromEntries(new FormData(form).entries())' in LOGIN
    assert "fetch('/api/login'" in LOGIN
    assert "credentials:'same-origin'" in LOGIN
    assert "window.location.assign('/dashboard')" in LOGIN
    assert "Invalid username or password." in LOGIN
    assert "Login UI is ready to connect" not in LOGIN
    assert "alert(" not in LOGIN
    assert 'href="{{ telegram_channel_url or support_url }}"' in LOGIN
    assert 'href="{{ support_url or telegram_channel_url }}"' in LOGIN
    assert 'href="#"' not in LOGIN

    # The only pre-script edits allowed to the uploaded design are the two
    # dynamic hrefs. Everything visual (CSS, structure, logo, inline SVGs)
    # must remain byte-for-byte identical to the supplied file.
    reference = Path("/home/user/uploads/titan-login-fixed.html")
    if reference.exists():
        original_visual = reference.read_text(encoding="utf-8").split("<script>", 1)[0]
        current_visual = LOGIN.split("<script>", 1)[0]
        current_visual = current_visual.replace(
            'href="{{ telegram_channel_url or support_url }}"', 'href="#"'
        ).replace(
            'href="{{ support_url or telegram_channel_url }}"', 'href="#"'
        )
        assert current_visual == original_visual


def test_dashboard_translates_every_static_visible_and_accessible_persian_string():
    ui = _parse_ui(DASHBOARD)
    values = list(dict.fromkeys(ui.persian_text + ui.translatable_attributes))
    translated = _dashboard_translate(values, "en")
    leftovers = [source for source, target in zip(values, translated, strict=True) if PERSIAN.search(target)]
    assert not leftovers, f"Persian UI copy remains in English mode: {leftovers}"

    examples = [
        "داشبورد", "Dashboard", "مصرف در 7 روز گذشته", "Last 7 days",
        "از 3 کاربر", "of 3 users", "Last 30 days", "۳۰ روز اخیر",
    ]
    english = _dashboard_translate(examples, "en")
    persian = _dashboard_translate(examples, "fa")
    assert english[0] == "Dashboard" and persian[1] == "داشبورد"
    assert english[2] == "Usage in the last 7 days"
    assert english[3] == "Last 7 days" and persian[3] == "۷ روز اخیر"
    assert english[4] == "of 3 users" and persian[5] == "از 3 کاربر"
    assert english[6] == "Last 30 days" and persian[6] == "۳۰ روز اخیر"
    default_password_notice = "رمز پیش‌فرض مدیر هنوز فعال است. برای امنیت، آن را با رمزی منحصربه‌فرد جایگزین کنید."
    assert _dashboard_translate([default_password_notice], "en") == [
        "The default administrator password is still in use. Replace it with a unique password for security."
    ]
    reverse_examples = _dashboard_translate(["Fingerprint", "Enable Fragment", "Updated"], "fa")
    assert reverse_examples == ["اثر انگشت", "فعال‌سازی فرگمنت", "به‌روزرسانی شد"]
    multiline = "از بین کانفیگ‌های ساخته‌شده انتخاب کن؛ هر چیزی که تیک بخورد داخل همین لینک می‌آید.\n          یک کاربر می‌تواند چند کانفیگ داشته باشد و چند کاربر می‌توانند در یک لینک جمع شوند."
    assert not PERSIAN.search(_dashboard_translate([multiline], "en")[0])
    # Dynamic dialogs/cards are also translated after the bridge inserts them.
    dynamic_fragments = [
        "سریع‌ترین مسیر از دستگاه شما: ", " با ", "هر لینکی که روی ", "پنل",
        " سرو شود، ",
        " دورتر از یک سرور نزدیک شماست — این همان چیزی است که «پینگِ قبلاً کمتر بود» را توضیح می‌دهد.",
        "بهترین نود شما ", "؛ تا کفِ ممکن ≈ ",
        "فاصله دارد. یک نود در همان شهرِ نزدیک (امارات/ترکیه) این فاصله را حذف می‌کند.",
        "پینگ‌سنج — اندازه‌گیری از همین دستگاه",
        "دامنه را بزن و ذخیره کن: نام، شهر، پرچم و کلید نود خودکار تشخیص داده می‌شود. اگر شناسایی ممکن نشد، فیلدهای دستی را پر کن و بعد متغیرها را با یک دکمه کپی کن.",
        "آخرین همگام‌سازی ناموفق بود (",
        ") — کاربران این نود از پنل سرو می‌شوند؛ توکن نود را روی خودِ نود ست کن (دکمهٔ ویرایش).",
        "هیچ‌کدام", "خاموش کردن", "روشن کردن", "همگام‌سازی فوری",
        "خروج از حالت نگهداری", "حالت نگهداری", "تصویر این لینک",
        "انگلیسی", "اثر انگشت", "آدرسی ثبت نشده", "هنوز کاربری ساخته نشده",
        "کانفیگی برای این کاربر ساخته نشده",
        "ساخت لینک اشتراک", "ویرایش لینک اشتراک", "نام اشتراک را بنویس",
        "حداقل یک کانفیگ انتخاب کن", "لینک ساخته شد و کپی شد ✓", "هیچ کانفیگی انتخاب نشده",
        "ذخیره شد، ولی نود قبول نکرد (", ") — فعلاً از پنل سرو می‌شود",
        "راه‌اندازی این نود", "نود جواب داد و کاربرانش را گرفت ✓",
        "این نود هنوز جواب نداده", "کپی شد", "همهٔ متغیرها کپی شد ✓",
        "کپی نشد — دستی انتخاب کن", "فهمیدم", "کانفیگ خاموش شد", "کانفیگ روشن شد",
        "سرور اصلی", "هنوز لینک اشتراکی ساخته نشده — با دکمهٔ بالا یکی بساز.",
        "لینک صفحهٔ اشتراک کپی شد", "تصویر این لینک برداشته شد", "لینک غیرفعال شد",
        "لینک فعال شد", "این لینک اشتراک حذف شود؟", "تغییر رمز",
        "رمز جدید باید حداقل ۶ کاراکتر باشد", "رمز عبور تغییر کرد", "رمز فعلی اشتباه است",
        "بازیابی از پشتیبان؟", "بازیابی شد", "راه‌اندازی مجدد پنل؟", "در حال راه‌اندازی...",
        "در حال تست...", "به‌روزرسانی شد", "خروج", "هلند — آمستردام", "هند — بمبئی",
    ]
    dynamic_en = _dashboard_translate(dynamic_fragments, "en")
    assert all(not PERSIAN.search(value) for value in dynamic_en), list(zip(dynamic_fragments, dynamic_en, strict=True))
    assert 'id="dashboardLanguage"' in DASHBOARD
    assert "data-i18n-ignore" in BRIDGE and "MutationObserver" in BRIDGE
    assert ".server-row .location" in BRIDGE, "server locations must remain location data, not translated UI"


def test_subscription_fa_en_dictionary_covers_static_and_accessibility_copy():
    fa, en = _subscription_dictionary_keys()
    assert fa == en
    ui = _parse_ui(SUBSCRIPTION)
    assert ui.keys <= fa
    assert ui.unannotated_persian == [], f"untranslated static Persian text: {ui.unannotated_persian}"
    assert "data-i18n=\"remaining\">باقی‌مانده" in SUBSCRIPTION
    assert "data-i18n=\"viewAll\"" in SUBSCRIPTION
    assert "unknownPlace:" in SUBSCRIPTION
    assert "data-i18n-alt=\"support\"" in SUBSCRIPTION
    assert "document.documentElement.dir=currentLang==='fa'?'rtl':'ltr'" in SUBSCRIPTION
    assert "state.profileName+' · '+(currentLang==='en'?'TiTaN Subscription':'اشتراک TiTaN')" in SUBSCRIPTION
    assert "function clientType(label)" in SUBSCRIPTION
    assert "tr('officialSource')" in SUBSCRIPTION
