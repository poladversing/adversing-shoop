"""The premium layer of the *served* dashboard.

`templates/dashboard.html` ships the shell and the styles, `static/js/titan-bridge.js`
fills it with real data — those two files are what the panel actually serves. These
tests pin the parts that were asked for: premium icon actions instead of buttons
carrying a Persian word (users / configs / subscriptions), a luxury server card per
node, and none of it breaking the render.
"""

import pathlib
import re
import shutil
import subprocess

import pytest

REPO = pathlib.Path(__file__).resolve().parent.parent
DASHBOARD = (REPO / "templates" / "dashboard.html").read_text(encoding="utf-8")
BRIDGE = (REPO / "static" / "js" / "titan-bridge.js").read_text(encoding="utf-8")

PERSIAN = re.compile(r"[\u0600-\u06FF]")


def _section(name: str) -> str:
    start = DASHBOARD.index(f'<section class="section-view" data-section="{name}">')
    end = DASHBOARD.find('<section class="section-view"', start + 10)
    return DASHBOARD[start:end if end > 0 else len(DASHBOARD)]


def _buttons(chunk: str) -> list[str]:
    return re.findall(r"<button\b.*?</button>", chunk, re.S)


def test_the_dashboard_ships_the_premium_layer():
    assert '<style id="titan-premium">' in DASHBOARD
    for cls in (".ico-btn", ".node-lux", ".nl-dial", ".nl-cap", "rg-fg", ".ping.good", ".sr-medal"):
        assert cls in DASHBOARD, f"{cls} is not styled in the served dashboard"


def test_the_dashboard_offers_only_superjinx_protocol_transports():
    assert "transportsByProtocol={vless:['ws','httpupgrade'],vmess:['ws'],trojan:['ws']}" in BRIDGE
    assert 'option value="httpupgrade">HTTPUpgrade</option>' in DASHBOARD
    served_ui = BRIDGE + DASHBOARD
    assert "xhttp" not in served_ui.lower()
    assert "grpc" not in served_ui.lower()
    assert "vless-ws-2" in BRIDGE and "vless-httpupgrade" in BRIDGE


def test_dashboard_ships_one_bridge_bundle_and_no_dead_legacy_assets():
    assert '/static/js/titan-bridge.js' in DASHBOARD
    assert '/static/js/ui.js' not in DASHBOARD
    assert '/static/js/pages.js' not in DASHBOARD
    for path in ("static/js/login.js", "static/js/ui.js", "static/js/pages.js", "static/img/favicon.png"):
        assert not (REPO / path).exists(), f"confirmed-unused asset still shipped: {path}"


@pytest.mark.parametrize("section", ["users", "configs", "servers"])
def test_the_primary_action_is_a_premium_icon(section):
    """The bridge binds to `.section-btn.primary`, so keep it and drop the word."""
    chunk = _section(section)
    head = chunk[: chunk.index("</div></div>") + 12]
    primary = [b for b in _buttons(head) if "section-btn primary" in b]
    assert primary, f"{section}: no primary action left"
    assert "ico-btn" in primary[0], f"{section}: the primary action is not an icon button"
    label = re.sub(r"<[^>]+>", "", primary[0]).strip()
    assert label == "", f"{section}: the primary action still shows the text {label!r}"
    assert "<svg" in primary[0], f"{section}: the primary action has no icon"


@pytest.mark.parametrize("section", ["users", "configs", "subscriptions"])
def test_no_action_button_in_those_sections_carries_a_persian_word(section):
    for btn in _buttons(_section(section)):
        classes = re.search(r'class="([^"]*)"', btn)
        classes = classes.group(1) if classes else ""
        # Filter chips ("فعال", "منقضی") stay words on purpose: an icon cannot say them.
        if "section-btn" in classes and "ico-btn" not in classes:
            continue
        text = re.sub(r"<[^>]+>", "", btn).strip()
        assert not PERSIAN.search(text), f"{section}: {text!r} is still written on the button"


def test_the_bridge_renders_icon_actions_and_luxury_cards():
    assert "function icoBtn(" in BRIDGE and "function nodeCard(" in BRIDGE
    assert "class=\"mini-btn\"" not in BRIDGE, "a row or modal still renders a text button"
    # the two actions the new tables added: QR and on/off, in both sections
    for act in ('"data-act":"qr"', '"data-act":"power"', '"data-act":"configs"'):
        assert act in BRIDGE, f"{act} is not wired"
    assert "openSubConfigModal" in BRIDGE and "openNodeSetupModal" in BRIDGE
    assert "const ICONS" in BRIDGE and BRIDGE.count("'<path") + BRIDGE.count("'<rect") >= 10


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_served_dashboard_renders_country_flags_from_code_or_location():
    """The loaded bridge must render actual flags, with a usable emoji fallback."""
    assert '/static/js/titan-bridge.js' in DASHBOARD
    for css in (".node-flag-visual", ".node-flag-loaded", ".node-flag-failed"):
        assert css in DASHBOARD, f"the served dashboard is missing {css} flag styling"
    script = r"""
const fs = require('fs');
const src = fs.readFileSync(process.env.TITAN_BRIDGE, 'utf8');
const start = src.indexOf('  function flagFor(cc)');
const end = src.indexOf('  // Dashboard copy is translated', start);
if (start < 0 || end < 0) throw new Error('live node flag renderer not found');
const fakeDocument = { addEventListener() {} };
const esc = value => String(value == null ? '' : value);
new Function('document', 'globalThis', 'esc', src.slice(start, end) + ';globalThis.flagHelpers={nodeCountryCode,nodeFlag,nodeFlagHtml};')(fakeDocument, globalThis, esc);
const cases = [
  [{ country_code: 'DE', flag: '🇳🇱' }, 'DE', '🇩🇪'],
  [{ country: 'Netherlands', city: 'Amsterdam' }, 'NL', '🇳🇱'],
  [{ city: 'Frankfurt' }, 'DE', '🇩🇪'],
  [{ country_code: 'UK' }, 'GB', '🇬🇧'],
  [{ flag: '🇸🇬' }, '', '🇸🇬'],
];
for (const [node, expectedCode, expectedFlag] of cases) {
  const code = globalThis.flagHelpers.nodeCountryCode(node);
  const flag = globalThis.flagHelpers.nodeFlag(node);
  if (code !== expectedCode || flag !== expectedFlag) {
    throw new Error(`${JSON.stringify(node)}: expected ${expectedCode}/${expectedFlag}, got ${code}/${flag}`);
  }
}
const html = globalThis.flagHelpers.nodeFlagHtml({ country: 'Germany' }, 'lg');
if (!html.includes('https://flagcdn.com/w80/de.png')) throw new Error(`missing country flag image: ${html}`);
if (!html.includes('🇩🇪') || !html.includes('node-flag-fallback')) throw new Error(`missing fallback flag: ${html}`);
"""
    result = subprocess.run(
        [shutil.which("node"), "-e", script], cwd=str(REPO), capture_output=True, text=True,
        env={"PATH": "/usr/bin:/bin:/usr/local/bin", "TITAN_BRIDGE": str(REPO / "static/js/titan-bridge.js")},
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_the_subscription_builder_and_node_detection_are_wired():
    """The two flows the admin asked for, pinned where they can regress.

    * A subscription link is *built*: the tab lists links, the modal offers every
      user with the configs that user can contribute, and saving posts the pick.
    * A node is added by its project domain: the modal can identify the domain,
      and the setup modal copies every variable with a single button when the
      node could not be claimed automatically.
    """
    assert "async function openSubBuilder(" in BRIDGE
    assert "/api/subscriptions/catalog" in BRIDGE
    for act in ('"data-act":"manage"', '"data-act":"copy"', '"data-act":"configs"'):
        assert act in BRIDGE, f"{act} is not wired into the new tables"
    assert "async function detectNode(" in BRIDGE and "/api/nodes/detect" in BRIDGE
    assert "setupCopyAll" in BRIDGE and "setup.block" in BRIDGE
    assert "'data-act':'claim'" in BRIDGE and "act==='claim'" in BRIDGE, \
        "a node cannot be detected/claimed in one click"
    assert "ساخت لینک اشتراک جدید" in DASHBOARD, "the subscriptions head has no new-link button"
    for cls in (".sub-user", ".sub-chip", ".sub-summary", ".det-card", ".det-ok"):
        assert cls in DASHBOARD, f"{cls} is not styled"


def test_the_latency_advisor_measures_from_the_client():
    """The ping that matters is client -> exit, which only a browser can measure.

    The panel's own node cards show panel -> node latency; that number is not the
    one a user feels. The advisor fires cache-busted no-store requests from the
    admin's browser at the panel edge, every enabled node and Cloudflare's nearest
    PoP, so the panel can tell which exit is actually closest to the admin.
    """
    assert "async function openLatencyAdvisor()" in BRIDGE
    assert "async function rttBest(" in BRIDGE and "'no-store'" in BRIDGE
    assert "cp.cloudflare.com/generate_204" in BRIDGE, "no Cloudflare floor to compare against"
    assert "'no-cors'" in BRIDGE, "node probes must be cross-origin safe"
    assert "x-railway-edge" in BRIDGE, "the serving region is not reported"
    # the header's pulse button is the entry point, and it is not a text button
    assert "data-act','advisor'" in BRIDGE
    assert 'data-tip="پینگ‌سنج' in DASHBOARD
    for cls in (".lat-row", ".lat-ms", ".lat-verdict", ".lat-custom"):
        assert cls in DASHBOARD, f"{cls} is not styled"


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_the_bridge_renders_every_section():
    """Runs the real bridge against a stub DOM and a real API shape."""
    r = subprocess.run([shutil.which("node"), str(REPO / "scripts" / "bridge_smoke.js")],
                       cwd=str(REPO), capture_output=True, text=True,
                       env={"PATH": "/usr/bin:/bin:/usr/local/bin", "TITAN_REPO": str(REPO)})
    assert r.returncode == 0, r.stdout + r.stderr
    assert "bridge rendered every section" in r.stdout


# ── the two things that were broken on a phone ───────────────────────────────

def test_no_template_leaks_its_own_source_into_the_page():
    """Nothing may follow `</html>`, and no markup may carry raw CSS.

    A block of CSS was appended to the dashboard *after* `</html>`. A browser has
    nowhere to put it except the body, so it painted thirty-odd lines of source
    code under the panel — and, because one of those lines was a single unbreakable
    string, the document became 521 px wide on a 360 px phone. Chrome then switched
    that phone out of its mobile layout to fit the page, which shrank the whole
    dashboard. One missing `</style>` and a document-wide layout change.
    """
    for template in sorted((REPO / "templates").glob("*.html")):
        text = template.read_text(encoding="utf-8")
        closes = list(re.finditer(r"</html\s*>", text, re.I))
        assert closes, f"{template.name}: not a complete document"
        tail = text[closes[-1].end():]
        assert not tail.strip(), f"{template.name} carries {len(tail)} chars after </html>: {tail.strip()[:80]!r}"

        # no `{prop:value}` pair may be sitting in text the browser would show
        stripped = re.sub(r"<(style|script)\b[^>]*>.*?</\1\s*>", " ", text, flags=re.S | re.I)
        stripped = re.sub(r"<!--.*?-->", " ", stripped, flags=re.S)
        stripped = re.sub(r"<[^>]+>", " ", stripped)
        leaks = re.findall(r"\{[^{}\n]{0,120}[:;][^{}\n]{0,120}\}", stripped)
        assert not leaks, f"{template.name}: CSS is visible as body text: {leaks[:2]}"


def test_the_dashboard_has_one_shipped_responsive_block():
    assert '<style id="titan-responsive">' in DASHBOARD
    block = DASHBOARD[DASHBOARD.index('<style id="titan-responsive">'):]
    block = block[: block.index("</style>")]
    for rule in (".header{height:auto", ".data-table,.subscription-table{min-width:0",
                 "overflow-x:auto", "font-size:16px"):
        assert rule in block, f"the phone layout lost {rule!r}"
    # the tablet band between 760 and 1100 px used to fall through every media query
    assert "@media (max-width:1100px)" in block


def test_login_uses_the_supplied_responsive_design():
    login = (REPO / "templates" / "login.html").read_text(encoding="utf-8")
    block = login[login.index("<style>"):]
    block = block[: block.index("</style>")]
    assert ".card{width:min(100%,405px)" in block
    assert "@media(max-width:480px)" in block
    assert "@media(max-width:340px)" in block
    assert "@media(prefers-reduced-motion:reduce)" in block
    assert '.brand-logo{width:88px;height:70px' in block
    assert "grid-template-columns:1fr 1fr" in block
    assert 'id="loginForm"' in login and 'id="togglePassword"' in login
    assert ".left{" not in block and ".right{" not in block


def test_every_page_asks_for_the_notch_area():
    for template in sorted((REPO / "templates").glob("*.html")):
        viewport = re.search(r'<meta name="viewport" content="([^"]+)"', template.read_text(encoding="utf-8"))
        assert viewport, f"{template.name}: no viewport meta"
        assert "width=device-width" in viewport.group(1), template.name


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_sidebar_drawer_gate_only_activates_for_real_mobile_mode():
    """Exercise the same detector with the four requested device/browser modes."""
    css = DASHBOARD[DASHBOARD.index('<style id="titan-real-mobile-sidebar">'):]
    css = css[: css.index("</style>")]
    assert "html.titan-real-mobile .sidebar{" in css
    assert "html.titan-real-mobile #menu:checked ~ .mobile-overlay" in css
    assert "html.titan-real-mobile #menu:checked ~ .mobile-toggle .toggle-open" in css
    assert "html.titan-real-mobile #menu:checked ~ .mobile-toggle .toggle-close" in css
    assert "z-index:115" in css and "z-index:120" in css, "overlay must sit behind the drawer"
    assert "html:not(.titan-real-mobile) .mobile-toggle .toggle-close{display:none}" in css
    assert '<label class="mobile-overlay" for="menu"' in DASHBOARD
    assert 'aria-controls="sidebar"' in DASHBOARD and '<aside class="sidebar" id="sidebar">' in DASHBOARD
    assert "@media (max-width:760px){" in DASHBOARD, "shared tablet breakpoint was removed"

    script = r"""
const fs = require('fs');
const html = fs.readFileSync(process.env.TITAN_TEMPLATE, 'utf8');
const match = html.match(/<script id="titan-real-mobile-detect">([\s\S]*?)<\/script>/);
if (!match) throw new Error('real-mobile detector not found');
function active({ua, width, touch, hint}) {
  const classes = new Set();
  const document = {documentElement: {classList: {
    toggle(name, enabled) { enabled ? classes.add(name) : classes.delete(name); }
  }}};
  const navigator = {userAgent: ua, maxTouchPoints: touch ? 5 : 0};
  if (hint !== undefined) navigator.userAgentData = {mobile: hint};
  const window = {
    matchMedia(query) {
      if (query !== '(max-width:760px) and (pointer:coarse) and (hover:none)') throw new Error(query);
      return {matches: width <= 760};
    },
    addEventListener() {}
  };
  new Function('navigator', 'window', 'document', match[1])(navigator, window, document);
  return classes.has('titan-real-mobile');
}
const modes = [
  ['real mobile, Desktop Mode off', {
    ua: 'Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 Mobile/15E148 Safari/604.1',
    width: 390, touch: true
  }, true],
  ['mobile with Desktop Mode on', {
    ua: 'Mozilla/5.0 (Linux; Android 14; Pixel 8 Pro) AppleWebKit/537.36 Chrome/126.0.0.0 Mobile Safari/537.36',
    width: 980, touch: true, hint: false
  }, false],
  ['tablet', {
    ua: 'Mozilla/5.0 (iPad; CPU OS 17_0 like Mac OS X) AppleWebKit/605.1.15 Version/17.0 Mobile/15E148 Safari/604.1',
    width: 744, touch: true
  }, false],
  ['laptop/desktop', {
    ua: 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126.0.0.0 Safari/537.36',
    width: 1440, touch: false, hint: false
  }, false]
];
for (const [name, profile, expected] of modes) {
  const actual = active(profile);
  if (actual !== expected) throw new Error(`${name}: expected ${expected}, got ${actual}`);
  console.log(`${name}: ${actual ? 'drawer enabled' : 'existing behavior preserved'}`);
}
"""
    result = subprocess.run(
        [shutil.which("node"), "-e", script], cwd=str(REPO), capture_output=True, text=True,
        env={"PATH": "/usr/bin:/bin:/usr/local/bin", "TITAN_TEMPLATE": str(REPO / "templates/dashboard.html")},
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "real mobile, Desktop Mode off: drawer enabled" in result.stdout
    assert "mobile with Desktop Mode on: existing behavior preserved" in result.stdout
    assert "tablet: existing behavior preserved" in result.stdout
    assert "laptop/desktop: existing behavior preserved" in result.stdout


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_header_spacing_gate_only_targets_phone_desktop_mode():
    """Keep the new header layout isolated to phone hardware in the desktop viewport band."""
    style_match = re.search(
        r'<style id="titan-mobile-desktop-header">([\s\S]*?)</style>', DASHBOARD
    )
    assert style_match, "the scoped header override is missing"
    css = re.sub(r"/\*.*?\*/", "", style_match.group(1), flags=re.S)
    selectors = [selector.strip() for selector in re.findall(r"([^{}]+)\{", css)]
    assert selectors, "the scoped header stylesheet is empty"
    assert all(selector.startswith("html.titan-mobile-desktop-mode") for selector in selectors), \
        f"an unscoped header rule could change real mobile/desktop: {selectors}"
    for rule in (
        'grid-template-areas:"search actions"',
        "grid-template-columns:minmax(160px,1fr) minmax(240px,1.15fr)",
        "column-gap:clamp(16px,2vw,24px)",
        "flex-wrap:nowrap",
        "justify-content:flex-end",
    ):
        assert rule in css, f"the desktop-mode header lost {rule!r}"

    script = r"""
const fs = require('fs');
const html = fs.readFileSync(process.env.TITAN_TEMPLATE, 'utf8');
const match = html.match(/<script id="titan-mobile-desktop-header-detect">([\s\S]*?)<\/script>/);
if (!match) throw new Error('mobile desktop-mode header detector not found');
function makeMode({ua='Mozilla/5.0', screenWidth, screenHeight, width, touch=false, coarse=touch}) {
  const classes = new Set();
  const listeners = {};
  const document = {documentElement: {classList: {
    toggle(name, enabled) { enabled ? classes.add(name) : classes.delete(name); }
  }}};
  const navigator = {userAgent: ua, maxTouchPoints: touch ? 5 : 0};
  const window = {
    screen: {width: screenWidth, height: screenHeight},
    matchMedia(query) {
      if (query === '(pointer:coarse)') return {matches: coarse};
      if (query === '(min-width:761px) and (max-width:1100px)') {
        return {get matches() { return width >= 761 && width <= 1100; }};
      }
      throw new Error('unexpected media query: ' + query);
    },
    addEventListener(name, callback) { listeners[name] = callback; }
  };
  new Function('navigator', 'window', 'document', match[1])(navigator, window, document);
  return {
    active: () => classes.has('titan-mobile-desktop-mode'),
    resize(nextWidth) { width = nextWidth; listeners.resize(); }
  };
}
const modes = [
  ['mobile, Desktop Mode off', {
    ua: 'Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) Mobile/15E148 Safari/604.1',
    screenWidth: 390, screenHeight: 844, width: 390, touch: true
  }, false],
  ['mobile Desktop Mode, mobile UA retained', {
    ua: 'Mozilla/5.0 (Linux; Android 14; Pixel 8 Pro) Mobile Safari/537.36',
    screenWidth: 412, screenHeight: 915, width: 980, touch: true
  }, true],
  ['mobile Desktop Mode, desktop UA', {
    ua: 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15) AppleWebKit/605.1.15 Safari/605.1.15',
    screenWidth: 393, screenHeight: 852, width: 980, touch: true
  }, true],
  ['tablet in desktop layout', {
    ua: 'Mozilla/5.0 (Linux; Android 14; SM-X700) AppleWebKit/537.36 Chrome/126.0.0.0 Safari/537.36',
    screenWidth: 800, screenHeight: 1280, width: 1000, touch: true
  }, false],
  ['touch laptop', {
    ua: 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126.0.0.0 Safari/537.36',
    screenWidth: 1024, screenHeight: 768, width: 1024, touch: true
  }, false],
  ['desktop/laptop', {
    ua: 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126.0.0.0 Safari/537.36',
    screenWidth: 1440, screenHeight: 900, width: 1440, touch: false
  }, false]
];
for (const [name, profile, expected] of modes) {
  const mode = makeMode(profile);
  if (mode.active() !== expected) throw new Error(`${name}: expected ${expected}, got ${mode.active()}`);
  console.log(`${name}: ${mode.active() ? 'header layout enabled' : 'existing behavior preserved'}`);
}
const resized = makeMode({screenWidth:390, screenHeight:844, width:390, touch:true});
resized.resize(980);
if (!resized.active()) throw new Error('resize into Desktop Mode did not activate the layout');
resized.resize(390);
if (resized.active()) throw new Error('resize back to normal mobile did not restore the existing layout');
console.log('resize transitions: passed');
"""
    result = subprocess.run(
        [shutil.which("node"), "-e", script], cwd=str(REPO), capture_output=True, text=True,
        env={"PATH": "/usr/bin:/bin:/usr/local/bin", "TITAN_TEMPLATE": str(REPO / "templates/dashboard.html")},
    )
    assert result.returncode == 0, result.stdout + result.stderr
    for expected in (
        "mobile, Desktop Mode off: existing behavior preserved",
        "mobile Desktop Mode, mobile UA retained: header layout enabled",
        "mobile Desktop Mode, desktop UA: header layout enabled",
        "tablet in desktop layout: existing behavior preserved",
        "touch laptop: existing behavior preserved",
        "desktop/laptop: existing behavior preserved",
        "resize transitions: passed",
    ):
        assert expected in result.stdout


def test_final_responsive_cascade_keeps_dashboard_cards_and_dialogs_compact():
    marker = '<style id="titan-responsive-final">'
    assert marker in DASHBOARD
    block = DASHBOARD[DASHBOARD.index(marker):]
    block = block[:block.index("</style>")]
    for rule in (
        ".stats .card{height:148px;min-height:136px}",
        "@media(max-width:820px)",
        "@media(max-width:560px)",
        ".section-grid,.section-grid.three,.form-grid{grid-template-columns:minmax(0,1fr)}",
        "overflow-x:auto;overscroll-behavior-x:contain",
        "#titanModal>div{box-sizing:border-box!important",
        "max-height:calc(100dvh - 20px)!important",
    ):
        assert rule in block, f"the final responsive cascade lost {rule!r}"


def test_short_sidebar_menu_scrolls_internally_only_when_the_viewport_is_short():
    css = (REPO / "static" / "css" / "dashboard-reference.css").read_text(encoding="utf-8")
    base_rules, short_rule = css.split("/* On short viewports, scroll only the menu", 1)
    nav_rule = re.search(r"\.nav\{([^}]*)\}", base_rules)
    assert nav_rule and "display:flex;flex:0 0 auto;" in nav_rule.group(0)
    assert not re.search(r"overflow(?:-y)?\s*:\s*(?:auto|scroll)", nav_rule.group(1))
    short_rule = "/* On short viewports, scroll only the menu" + short_rule
    assert "@media(max-height:820px)" in short_rule
    assert "overflow-y:auto" in short_rule
    assert "flex:0 1 auto" in short_rule and "min-height:0" in short_rule
    assert "@media(min-width:901px) and (max-height:820px)" in short_rule
    assert ".sidebar-foot{margin-top:auto!important}" in short_rule
    assert ".sidebar .nav" in short_rule


def test_root_landing_is_a_fullscreen_matrix_screen_with_no_panel_shortcuts():
    landing = (REPO / "static" / "landing" / "index.html").read_text(encoding="utf-8")
    assert '<title>TiTaN</title>' in landing
    assert 'id="matrixCanvas"' in landing
    assert "position:fixed;inset:0" in landing
    assert "devicePixelRatio" in landing and "visualViewport" in landing
    assert "const glyphs=" in landing and "ctx.fillText" in landing
    assert "prefers-reduced-motion: reduce" in landing
    # The reference is intentionally a quiet, code-only gate: no visible login/dashboard
    # buttons, automatic navigation, remote assets, or timed redirect to the panel.
    assert "<a " not in landing.lower()
    assert "<button" not in landing.lower()
    assert "<form" not in landing.lower()
    assert not re.search(r"(?:href|src)=['\"]https?://", landing, re.I)
    assert 'href="/login"' not in landing and 'href="/dashboard"' not in landing
    assert "http-equiv=\"refresh\"" not in landing.lower()
    assert "location.href" not in landing and "location.assign" not in landing
