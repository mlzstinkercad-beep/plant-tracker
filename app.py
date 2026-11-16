"""
Plant Growth Tracker
--------------------
Take photos of a plant -> the AI tells you what it is, how healthy it is, how far it
has grown and what it needs. Photos are saved to a growth log with charts. The
Reminders tab sends real phone notifications (through the free ntfy app). The Fun
tab makes a joke about any picture.

Works in English, Hindi, Telugu, Tamil and Kannada (see translations.py).

Files:  app.py (screens) | ai.py (talks to the AI) | notifier.py (phone notifications)
        reminders.py (when is a task due) | translations.py (all the words)

Run with:  python -m streamlit run app.py

Showing it to other people on THEIR devices? Start it in public mode (see README):
every visitor then gets private temporary data, and your AI key stays on your computer.
"""

import base64
import copy
import io
import json
import os
import re
import shutil
import time
import uuid
from datetime import date, datetime
from pathlib import Path

import streamlit as st
from PIL import Image, ImageOps

import ai
import notifier
from reminders import (
    days_left,
    ensure_reminder,
    load_reminders,
    make_ics,
    read_json,
    save_reminders,
    write_json_atomic,
)
from translations import AI_NAMES, DEMO_BASE, DEMO_JOKE, DEMO_TEXT, LANGUAGES, UI

# ----------------------------------------------------------------------------
# Settings
# ----------------------------------------------------------------------------
def get_secret(name):
    """A setting kept on the host computer (environment variable or Streamlit secrets).
    These are used by the server only and are never sent to a visitor's browser."""
    value = os.environ.get(name)
    if value:
        return value
    try:
        value = st.secrets.get(name)
    except Exception:
        return None
    return str(value) if value else None


# PUBLIC MODE is for when other people open the app on their own devices:
# every visitor gets private, temporary data, and phone notifications are switched off.
PUBLIC_MODE = (get_secret("PLANT_PUBLIC") or "").strip().lower() in ("1", "true", "yes", "on")
try:
    MAX_SHARED_CALLS = int(get_secret("PLANT_MAX_CALLS") or 15)  # AI tries per visitor on your key
except ValueError:
    MAX_SHARED_CALLS = 15
MAX_UPLOAD_MB = 15
SESSIONS_DIR = Path("data") / "sessions"
SESSION_HOURS = 12  # a visitor's temporary folder is deleted after this long


def forget_old_visitors():
    """Delete the temporary folders of visitors who left a long time ago."""
    if not SESSIONS_DIR.exists():
        return
    too_old = time.time() - SESSION_HOURS * 3600
    for folder in SESSIONS_DIR.iterdir():
        try:
            if folder.is_dir() and folder.stat().st_mtime < too_old:
                shutil.rmtree(folder, ignore_errors=True)
        except OSError:
            pass


def data_folder():
    """On your own computer: one 'data' folder. In public mode: a private folder per visitor."""
    if PUBLIC_MODE:
        if "visitor_id" not in st.session_state:
            st.session_state["visitor_id"] = uuid.uuid4().hex[:16]
            forget_old_visitors()
        folder = SESSIONS_DIR / st.session_state["visitor_id"]
    else:
        folder = Path("data")
    (folder / "images").mkdir(parents=True, exist_ok=True)
    return folder


DATA_DIR = data_folder()
IMAGE_DIR = DATA_DIR / "images"
LOG_FILE = DATA_DIR / "log.json"
REMINDER_FILE = DATA_DIR / "reminders.json"

MAX_SIDE = {"fast": 768, "accurate": 1280}  # photos are shrunk to this before sending
MAX_PHOTOS = 3
SHOW_FIRST = 5  # how many check-ups the timeline shows before "Show all"

# ----------------------------------------------------------------------------
# Language helpers
# ----------------------------------------------------------------------------
def current_lang():
    return st.session_state.get("lang_code", "en")


def text_in(lang, key):
    """Screen text for one specific language (falls back to English)."""
    return UI.get(lang, {}).get(key) or UI["en"].get(key) or key


def t(key, **values):
    """Screen text in the language chosen in the sidebar."""
    text = text_in(current_lang(), key)
    return text.format(**values) if values else text


def label_for(prefix, value):
    """Translate fixed AI words like 'Healthy' or 'Seedling'. Unknown words stay as they are."""
    key = f"{prefix}_{value}"
    return UI.get(current_lang(), {}).get(key) or UI["en"].get(key) or str(value)


def build_demo(code):
    report = copy.deepcopy(DEMO_BASE)
    report.update(copy.deepcopy(DEMO_TEXT.get(code, DEMO_TEXT["en"])))
    return report


# ----------------------------------------------------------------------------
# Data helpers
# ----------------------------------------------------------------------------
@st.cache_data(show_spinner=False)
def _read_log(path, mtime_ns, size):
    data = read_json(path, [])
    return data if isinstance(data, list) else []


def load_log():
    """Read the growth log. It is only re-read from disk when the file changes."""
    if not LOG_FILE.exists():
        return []
    info = LOG_FILE.stat()
    return _read_log(str(LOG_FILE), info.st_mtime_ns, info.st_size)


def save_log(entries):
    write_json_atomic(LOG_FILE, entries)
    _read_log.clear()


@st.cache_data(show_spinner=False, max_entries=24)
def prepare_jpeg(raw, max_side):
    """Fix rotation and shrink the photo. Smaller photos upload and analyze faster."""
    img = Image.open(io.BytesIO(raw))
    if img.format == "JPEG":
        img.draft("RGB", (max_side * 2, max_side * 2))  # decode big phone photos faster
    try:
        img = ImageOps.exif_transpose(img)
    except Exception:
        pass
    img = img.convert("RGB")
    img.thumbnail((max_side, max_side))
    buffer = io.BytesIO()
    img.save(buffer, format="JPEG", quality=85)
    return buffer.getvalue()


def make_thumb(jpeg, side=480):
    img = Image.open(io.BytesIO(jpeg))
    img.thumbnail((side, side))
    buffer = io.BytesIO()
    img.save(buffer, format="JPEG", quality=80)
    return buffer.getvalue()


def keep_small(files):
    """Drop photos that are too big to be worth processing."""
    files = [f for f in files if f is not None]
    kept = [f for f in files if f.size <= MAX_UPLOAD_MB * 1024 * 1024]
    if len(kept) < len(files):
        st.warning(t("too_big", mb=MAX_UPLOAD_MB))
    return kept


def prepare_all(files, side):
    """Shrink photos. A broken file shows a message instead of crashing the app."""
    jpegs = []
    for f in files:
        try:
            jpegs.append(prepare_jpeg(f.getvalue(), side))
        except Exception as e:
            st.error(t("error", e=e))
    return jpegs


def take_ai_call():
    """Count one AI call. False when a visitor using YOUR key has used up their share."""
    if demo_mode or not (PUBLIC_MODE and using_server_key):
        return True
    used = st.session_state.get("ai_calls", 0)
    if used >= MAX_SHARED_CALLS:
        return False
    st.session_state["ai_calls"] = used + 1
    return True


@st.cache_data(show_spinner=False, max_entries=32)
def cached_plant_analysis(jpegs, provider, api_key, language_name, quality, hint):
    """Same photos + same settings = instant answer (no new AI call)."""
    return ai.analyze_plant(provider, api_key, list(jpegs), language_name, quality, hint)


def health_colour(score):
    if score >= 75:
        return "green"
    if score >= 45:
        return "orange"
    return "red"


def safe_filename(name):
    cleaned = "".join(c if c.isalnum() else "_" for c in name).strip("_")
    return cleaned or "plant"


def egg_key(text):
    return re.sub(r"[^a-z0-9]", "", str(text).lower())


# ----------------------------------------------------------------------------
# Drawing the report
# ----------------------------------------------------------------------------
def show_report(r, show_lang_note=False):
    """Display the AI report nicely."""
    st.subheader(f"🌿 {r.get('plant_name', '?')}")
    if show_lang_note and r.get("_lang") and r["_lang"] != current_lang():
        st.info(t("lang_note"))
    st.caption(f"{t('confidence')}: {label_for('conf', r.get('confidence', '?'))}")

    try:
        score = int(r.get("health_score", 0) or 0)
    except (TypeError, ValueError):
        score = 0
    c1, c2 = st.columns(2)
    c1.metric(t("health_score"), f"{score}/100")
    c2.metric(t("growth_stage"), label_for("stage", r.get("growth_stage", "?")))
    st.progress(min(max(score, 0), 100) / 100)
    status = label_for("status", r.get("health_status", "?"))
    st.markdown(f"**{t('status')}:** :{health_colour(score)}[{status}]")

    if r.get("estimated_height_cm"):
        st.write(t("est_height", h=r["estimated_height_cm"]))

    if r.get("observations"):
        st.markdown(f"**{t('see')}**")
        for item in r["observations"]:
            st.write(f"- {item}")

    if r.get("problems"):
        st.markdown(f"**{t('problems')}**")
        for item in r["problems"]:
            st.write(f"- {item}")

    needs = r.get("needs", {})
    st.markdown(f"**{t('needs')}**")
    n1, n2 = st.columns(2)
    n1.info(f"**{t('water')}**\n\n{needs.get('water', '-')}")
    n2.warning(f"**{t('sunlight')}**\n\n{needs.get('sunlight', '-')}")
    n3, n4 = st.columns(2)
    n3.success(f"**{t('soil')}**\n\n{needs.get('soil', '-')}")
    n4.error(f"**{t('fertilizer')}**\n\n{needs.get('fertilizer', '-')}")

    if r.get("next_steps"):
        st.markdown(f"**{t('next_steps')}**")
        for item in r["next_steps"]:
            st.write(f"- {item}")

    if r.get("photo_tip"):
        st.caption(f"{t('photo_tip')}: {r['photo_tip']}")
    if r.get("fun_fact"):
        st.caption(f"💡 {r['fun_fact']}")


# ----------------------------------------------------------------------------
# Secret screens: black screen + your picture (or text) + buttons
# ----------------------------------------------------------------------------
PICTURE_TYPES = (".png", ".jpg", ".jpeg", ".webp")


def find_picture(base_name):
    """Look for base_name.png / .jpg / .jpeg / .webp next to app.py."""
    for ext in PICTURE_TYPES:
        path = Path(base_name + ext)
        if path.exists():
            return path
    return None


def picture_as_html(path):
    """Shrink the picture and put it inside the page. Returns None if it cannot be read."""
    try:
        picture = Image.open(path)
        picture.thumbnail((1000, 1000))
        buffer = io.BytesIO()
        if picture.mode in ("RGBA", "LA", "P"):
            picture.convert("RGBA").save(buffer, format="PNG")
            mime = "image/png"
        else:
            picture.convert("RGB").save(buffer, format="JPEG", quality=88)
            mime = "image/jpeg"
        data = base64.b64encode(buffer.getvalue()).decode("ascii")
        return f'<img class="egg-img" src="data:{mime};base64,{data}" alt="">'
    except Exception:
        return None


def leave_egg():
    st.session_state["egg_on"] = False
    st.session_state["nickname"] = ""


SECRET_CSS = (
    "<style>"
    ".stApp{background:__BG__ !important;}"
    '[data-testid="stSidebar"],[data-testid="stHeader"],[data-testid="stToolbar"],'
    '[data-testid="stSidebarCollapsedControl"],footer{display:none !important;}'
    ".block-container{padding-top:0 !important;}"
    ".egg-wrap{height:72vh;display:flex;flex-direction:column;align-items:center;"
    "justify-content:center;text-align:center;}"
    ".egg-img{max-width:88%;max-height:60vh;object-fit:contain;filter:drop-shadow(0 0 22px __GLOW__);}"
    ".egg-title{font-family:Georgia,'Times New Roman',serif;__TITLE__}"
    ".egg-hint{margin-top:1.2rem;max-width:80%;font-family:sans-serif;font-size:.85rem;color:#8a8a8a;}"
    "@keyframes flick{0%,18%,22%,24%,54%,57%,100%{opacity:1}20%,23%,55%{opacity:.2}}"
    "@keyframes shine{to{background-position:200% center}}"
    ".spore{position:fixed;bottom:-10px;width:4px;height:4px;border-radius:50%;"
    "background:__SPORE__;animation:rise linear infinite;}"
    "@keyframes rise{0%{transform:translateY(0);opacity:0}12%{opacity:.8}"
    "100%{transform:translateY(-110vh);opacity:0}}"
    ".stButton>button{background:#000;color:__ACCENT__;border:1px solid __BORDER__;}"
    ".stButton>button:hover{background:__HOVER__;color:#fff;border-color:__ACCENT__;}"
    "</style>"
)

SECRET_SCREENS = {
    # Typing one of the "triggers" as the plant name opens the screen.
    # Triggers are letters and numbers only, no spaces or capitals.
    "stranger": {
        "triggers": {"strangerthings"},
        "picture": "egg_image",  # save your own picture with this name (.png .jpg .jpeg .webp)
        "style": {
            "bg": "#000",
            "accent": "#ff4040",
            "border": "#ff2a2a",
            "hover": "#2a0000",
            "glow": "rgba(255,0,0,.45)",
            "spore": "rgba(255,255,255,.4)",
            "title": (
                "font-size:clamp(1.8rem,8vw,4.5rem);letter-spacing:.18em;color:#ff2a2a;"
                "text-shadow:0 0 8px #ff0000,0 0 26px #b30000,0 0 70px #7a0000;"
                "animation:flick 3.2s infinite;"
            ),
        },
    },
    "swag": {
        "triggers": {"swag", "rajini", "rajni", "rajinikanth", "rajnikanth", "thalaivar", "thalaiva"},
        "picture": "swag_image",
        "text": "SWAG",
        "hint": True,
        "style": {
            "bg": "radial-gradient(circle at 50% 38%,#3b2a00 0%,#000 62%)",
            "accent": "#ffd34d",
            "border": "#e0a800",
            "hover": "#2a2000",
            "glow": "rgba(255,200,0,.5)",
            "spore": "rgba(255,210,80,.65)",
            "title": (
                "font-weight:700;font-size:clamp(3rem,16vw,8rem);letter-spacing:.12em;"
                "background:linear-gradient(90deg,#b8860b,#ffd700,#fff6c2,#ffd700,#b8860b);"
                "background-size:200% auto;-webkit-background-clip:text;background-clip:text;"
                "color:transparent;animation:shine 3s linear infinite;"
                "filter:drop-shadow(0 0 14px rgba(255,200,0,.45));"
            ),
        },
    },
}

FULLSCREEN_BUTTON = """<html><body style="margin:0;background:transparent">
<button id="fs" style="width:100%;height:38px;background:#000;color:__ACCENT__;border:1px solid __BORDER__;border-radius:8px;font-size:15px;cursor:pointer;font-family:sans-serif"></button>
<script>
const doc = window.parent.document, btn = document.getElementById('fs');
const labels = __LABELS__;
function refresh() { btn.textContent = doc.fullscreenElement ? labels.exit : labels.enter; }
if (!doc.documentElement.requestFullscreen) { btn.style.display = 'none'; }
btn.addEventListener('click', function () {
  if (doc.fullscreenElement) { doc.exitFullscreen(); } else { doc.documentElement.requestFullscreen(); }
});
doc.addEventListener('fullscreenchange', refresh);
refresh();
</script></body></html>"""


def embed_html(html, height):
    """Show a small piece of HTML with a script in it (new Streamlit first, older one as backup)."""
    if hasattr(st, "iframe"):
        st.iframe(html, height=height)
    else:
        import streamlit.components.v1 as components

        components.html(html, height=height)


def show_secret(screen_id):
    """Black screen, your picture (or text), a full-screen button and a way back."""
    cfg = SECRET_SCREENS[screen_id]
    style = cfg["style"]
    spores = "".join(
        f'<span class="spore" style="left:{(i * 37) % 100}%;'
        f"animation-delay:{(i * 0.9) % 7:.1f}s;animation-duration:{8 + (i * 5) % 7}s\"></span>"
        for i in range(18)
    )
    css = SECRET_CSS
    for token, value in (
        ("__BG__", style["bg"]),
        ("__ACCENT__", style["accent"]),
        ("__BORDER__", style["border"]),
        ("__HOVER__", style["hover"]),
        ("__GLOW__", style["glow"]),
        ("__SPORE__", style["spore"]),
        ("__TITLE__", style["title"]),
    ):
        css = css.replace(token, value)

    path = find_picture(cfg["picture"])
    centre = picture_as_html(path) if path else None
    if centre is None:  # no picture (or unreadable): show big text instead
        title = cfg.get("text") or t("egg_text")
        centre = f'<div class="egg-title">{title}</div>'
        if cfg.get("hint"):
            centre += f'<div class="egg-hint">{t("swag_hint")}</div>'

    st.markdown(css + spores + f'<div class="egg-wrap">{centre}</div>', unsafe_allow_html=True)
    left, middle, right = st.columns([1, 2, 1])
    with middle:
        st.button(t("egg_exit"), on_click=leave_egg, key="egg_exit", width="stretch")
        labels = json.dumps({"enter": t("fs_enter"), "exit": t("fs_exit")}, ensure_ascii=False)
        embed_html(
            FULLSCREEN_BUTTON.replace("__ACCENT__", style["accent"])
            .replace("__BORDER__", style["border"])
            .replace("__LABELS__", labels),
            height=44,
        )


# ----------------------------------------------------------------------------
# The app screen
# ----------------------------------------------------------------------------
st.set_page_config(page_title="Plant Growth Tracker", page_icon="🌱", layout="centered")

with st.sidebar:
    lang_name = st.selectbox(
        "🌐 Language / भाषा / భాష / மொழி / ಭಾಷೆ", list(LANGUAGES), key="lang_name"
    )
    st.session_state["lang_code"] = LANGUAGES[lang_name]

    st.header(t("settings"))
    provider = st.selectbox(
        t("provider"),
        ["gemini", "claude"],
        format_func=lambda p, lang=current_lang(): text_in(lang, "provider_" + p),
        key="provider",
    )
    # A key set on the host computer is used by the server only. It is never put in the page.
    server_key = get_secret("GEMINI_API_KEY" if provider == "gemini" else "ANTHROPIC_API_KEY")
    if provider == "gemini":
        typed_key = st.text_input(
            t("gemini_key"), type="password", help=t("gemini_help"), key="gemini_key"
        )
        st.caption(t("free_note"))
    else:
        typed_key = st.text_input(
            t("api_key"), type="password", help=t("api_help"), key="api_key"
        )
    quality = st.radio(
        t("quality"),
        ["fast", "accurate"],
        format_func=lambda q, lang=current_lang(): text_in(lang, "quality_" + q),
        key="quality",
    )
    typed_key = typed_key.strip()
    api_key = typed_key or server_key or ""
    using_server_key = not typed_key and bool(server_key)
    demo_mode = not api_key
    if demo_mode:
        st.warning(t("demo_warn"))
    elif using_server_key:
        st.success(t("shared_on"))
    else:
        st.success(t("ai_on"))


@st.cache_resource
def start_notifier():
    """Starts the helper that sends phone notifications (once per app run)."""
    return notifier.start_background(DATA_DIR)


if not PUBLIC_MODE:  # notifications need your own computer
    start_notifier()

# Secret screens: shown when the plant name matches a trigger. Everything else is hidden.
typed_name = egg_key(st.session_state.get("nickname", ""))
for screen_id, screen in SECRET_SCREENS.items():
    if typed_name in screen["triggers"]:
        st.session_state["egg_on"] = screen_id
if st.session_state.get("egg_on") in SECRET_SCREENS:
    show_secret(st.session_state["egg_on"])
    st.stop()

st.title(t("title"))
st.write(t("subtitle"))

tab_scan, tab_growth, tab_remind, tab_fun = st.tabs(
    [t("tab_scan"), t("tab_growth"), t("tab_remind"), t("tab_fun")]
)

# ---- Tab 1: scan ----------------------------------------------------------
with tab_scan:
    plant_nickname = st.text_input(
        t("nickname"), placeholder=t("nickname_ph"), key="nickname"
    )
    plant_hint = st.text_input(t("hint_label"), key="plant_hint")

    source = st.radio(
        t("photo_source"),
        ["camera", "upload"],
        format_func=lambda s, lang=current_lang(): text_in(lang, "src_" + s),
        horizontal=True,
        key="source",
    )
    if source == "camera":
        shot = st.camera_input(t("cam_label"), key="cam")
        photos = [shot] if shot is not None else []
    else:
        photos = st.file_uploader(
            t("upload_label"),
            type=["jpg", "jpeg", "png", "webp"],
            accept_multiple_files=True,
            key="upl",
        )
        photos = list(photos or [])[:MAX_PHOTOS]
        st.caption(t("multi_note"))

    jpegs = prepare_all(keep_small(photos), MAX_SIDE[quality]) if photos else []
    if jpegs:
        st.image(jpegs, width=180)

        if st.button(t("analyze"), type="primary"):
            if not plant_nickname.strip():
                st.error(t("need_name"))
            elif not take_ai_call():
                st.error(t("limit_reached"))
            else:
                with st.spinner(t("looking")):
                    try:
                        code = current_lang()
                        if demo_mode:
                            report = build_demo(code)
                        else:
                            report = dict(
                                cached_plant_analysis(
                                    tuple(jpegs),
                                    provider,
                                    api_key,
                                    AI_NAMES[code],
                                    quality,
                                    plant_hint.strip(),
                                )
                            )
                        report["_lang"] = code
                        st.session_state["report"] = report
                        st.session_state["images"] = jpegs
                        st.session_state["plant_for_report"] = plant_nickname.strip()
                    except ai.ServiceBusy:
                        st.error(t("rate_error"))
                    except Exception as e:
                        st.error(t("error", e=e))

    if "report" in st.session_state:
        show_report(st.session_state["report"], show_lang_note=True)
        if st.button(t("save")):
            entry_id = uuid.uuid4().hex[:10]
            first_photo = st.session_state["images"][0]
            image_path = IMAGE_DIR / f"{entry_id}.jpg"
            thumb_path = IMAGE_DIR / f"{entry_id}_thumb.jpg"
            image_path.write_bytes(first_photo)
            thumb_path.write_bytes(make_thumb(first_photo))
            plant = st.session_state["plant_for_report"]

            entries = list(load_log())
            entries.append(
                {
                    "id": entry_id,
                    "plant": plant,
                    "date": datetime.now().strftime("%Y-%m-%d %H:%M"),
                    "image": str(image_path),
                    "thumb": str(thumb_path),
                    "report": st.session_state["report"],
                }
            )
            save_log(entries)

            reminders = load_reminders(REMINDER_FILE)
            ensure_reminder(reminders, plant, st.session_state["report"])
            save_reminders(REMINDER_FILE, reminders)

            st.success(t("saved"))

# ---- Tab 2: growth log ----------------------------------------------------
with tab_growth:
    entries = load_log()
    if not entries:
        st.info(t("no_entries"))
    else:
        plants = sorted({e["plant"] for e in entries})
        chosen = st.selectbox(t("choose_plant"), plants, key="chosen_plant")
        history = [e for e in entries if e["plant"] == chosen]
        history.sort(key=lambda e: e["date"])

        st.write(t("checkups", n=len(history), p=chosen))

        if len(history) >= 2:
            st.markdown(f"**{t('chart_health')}**")
            st.line_chart(
                {t("health_score"): [e["report"].get("health_score", 0) for e in history]}
            )
            heights = [e["report"].get("estimated_height_cm") for e in history]
            if all(h is not None for h in heights):
                st.markdown(f"**{t('chart_height')}**")
                st.line_chart({"cm": heights})
        else:
            st.caption(t("need_two"))

        st.markdown(f"### {t('timeline')}")
        newest_first = list(reversed(history))
        show_everything = st.session_state.get("show_all_" + chosen, False)
        visible = newest_first if show_everything else newest_first[:SHOW_FIRST]
        for e in visible:
            rep = e["report"]
            title = (
                f"{e['date']}  |  {label_for('status', rep.get('health_status', '?'))}"
                f"  |  {label_for('stage', rep.get('growth_stage', '?'))}"
            )
            with st.expander(title):
                picture = Path(e.get("thumb") or e["image"])
                if picture.exists():
                    st.image(str(picture))
                show_report(rep)
        if len(newest_first) > len(visible):
            if st.button(t("show_all", n=len(newest_first)), key="show_all_btn"):
                st.session_state["show_all_" + chosen] = True
                st.rerun()

        if st.button(t("delete_all", p=chosen)):
            for e in history:
                Path(e["image"]).unlink(missing_ok=True)
                if e.get("thumb"):
                    Path(e["thumb"]).unlink(missing_ok=True)
            save_log([e for e in entries if e["plant"] != chosen])
            reminders = load_reminders(REMINDER_FILE)
            reminders.pop(chosen, None)
            save_reminders(REMINDER_FILE, reminders)
            st.session_state.pop("chosen_plant", None)
            st.rerun()

# ---- Tab 3: reminders -----------------------------------------------------
# (short name, interval key, last-done key, task label, interval label, done button, max days)
TASKS = [
    ("water", "water_every", "last_water", "rem_task_water", "rem_water_every", "rem_done_water", 60),
    ("fert", "fert_every", "last_fert", "rem_task_fert", "rem_fert_every", "rem_done_fert", 120),
]

def show_notification_settings():
    """The phone-notification box (ntfy). Only used on your own computer."""
    settings = notifier.load_settings(DATA_DIR)
    with st.expander(t("notif_title"), expanded=True):
        st.write(t("notif_intro"))
        st.write(t("notif_step1"))
        st.write(t("notif_step2"))
        st.code(settings["topic"], language=None)
        st.write(t("notif_step3"))
        notif_enabled = st.checkbox(
            t("notif_on"), value=bool(settings["enabled"]), key="notif_on"
        )
        notif_hour = st.number_input(
            t("notif_hour"), min_value=0, max_value=23, value=int(settings["hour"]), key="notif_hour"
        )
        if st.button(t("notif_test"), key="notif_test"):
            try:
                notifier.send_ntfy(settings["topic"], t("notif_msg_title"), t("notif_test_msg"))
                st.success(t("notif_test_ok"))
            except Exception as e:
                st.error(t("notif_test_fail", e=e))
        st.caption(t("notif_running"))
        st.caption(t("notif_secret"))
    new_settings = {
        **settings,
        "enabled": bool(notif_enabled),
        "hour": int(notif_hour),
        "lang": current_lang(),
    }
    if new_settings != settings:
        notifier.save_settings(DATA_DIR, new_settings)


with tab_remind:
    if not PUBLIC_MODE:  # phone notifications need your own computer
        show_notification_settings()

    # ---- the to-do list
    reminders = load_reminders(REMINDER_FILE)
    if not reminders:
        st.info(t("rem_none"))
    else:
        st.write(t("rem_intro"))

        todo = []
        for plant, r in sorted(reminders.items()):
            for short, every_key, last_key, label_key, *_ in TASKS:
                if days_left(r[last_key], r[every_key]) <= 0:
                    todo.append(f"🌿 **{plant}** - {t(label_key)}")
        st.subheader(t("rem_summary"))
        if todo:
            for line in todo:
                st.write(f"- {line}")
        else:
            st.success(t("rem_all_good"))
        st.divider()

        changed = False
        for plant, r in sorted(reminders.items()):
            st.markdown(f"### 🌿 {plant}")
            for col, task in zip(st.columns(2), TASKS):
                short, every_key, last_key, label_key, every_label_key, done_key, max_days = task
                with col:
                    st.markdown(f"**{t(label_key)}**")
                    left = days_left(r[last_key], r[every_key])
                    if left < 0:
                        st.write(t("rem_overdue", d=-left))
                    elif left == 0:
                        st.write(t("rem_today"))
                    else:
                        st.write(t("rem_in", d=left))
                    st.caption(t("rem_last", date=r[last_key]))

                    new_every = st.number_input(
                        t(every_label_key),
                        min_value=1,
                        max_value=max_days,
                        value=min(max(int(r[every_key]), 1), max_days),
                        key=f"{short}_every_{plant}",
                    )
                    if int(new_every) != r[every_key]:
                        r[every_key] = int(new_every)
                        changed = True

                    if st.button(t(done_key), key=f"done_{short}_{plant}"):
                        r[last_key] = date.today().isoformat()
                        changed = True

            with st.expander(t("rem_cal_expander")):
                st.caption(t("rem_cal_help"))
                ics_text = make_ics(plant, r, t("rem_task_water"), t("rem_task_fert"))
                st.download_button(
                    t("rem_cal"),
                    data=ics_text.encode("utf-8"),
                    file_name=f"{safe_filename(plant)}_reminders.ics",
                    mime="text/calendar",
                    key=f"ics_{plant}",
                )
            st.divider()

        if changed:
            save_reminders(REMINDER_FILE, reminders)
            st.rerun()

# ---- Tab 4: fun -----------------------------------------------------------
with tab_fun:
    st.write(t("fun_intro"))
    st.caption(t("fun_people_note"))
    st.caption(t("fun_consent"))
    fun_file = st.file_uploader(
        t("fun_upload"), type=["jpg", "jpeg", "png", "webp"], key="fun_upl"
    )
    fun_hint = st.text_input(t("fun_hint"), key="fun_hint")
    fun_jpegs = prepare_all(keep_small([fun_file]), MAX_SIDE["fast"]) if fun_file is not None else []
    if fun_jpegs:
        fun_jpeg = fun_jpegs[0]
        st.image(fun_jpeg, width=300)
        fun_clicked = st.button(t("fun_button"), type="primary", key="fun_go")
        if fun_clicked and not take_ai_call():
            st.error(t("limit_reached"))
        elif fun_clicked:
            with st.spinner(t("fun_looking")):
                try:
                    code = current_lang()
                    if demo_mode:
                        fun_result = dict(DEMO_JOKE.get(code, DEMO_JOKE["en"]))
                    else:
                        fun_result = ai.joke_about_picture(
                            provider, api_key, fun_jpeg, AI_NAMES[code], "fast", fun_hint
                        )
                    st.session_state["fun_result"] = fun_result
                except ai.ServiceBusy:
                    st.error(t("rate_error"))
                except Exception as e:
                    st.error(t("error", e=e))

    fun_result = st.session_state.get("fun_result")
    if fun_result:
        st.markdown(f"**{t('fun_see')}**")
        st.write(fun_result.get("what_i_see", ""))
        if fun_result.get("recognized"):
            st.markdown(f"**{t('fun_recognized')}:** {fun_result['recognized']}")
        st.markdown(f"**{t('fun_joke')}**")
        st.success(fun_result.get("joke", ""))
        if fun_result.get("caption_idea"):
            st.caption(f"{t('fun_caption')}: {fun_result['caption_idea']}")
