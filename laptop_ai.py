"""
LAPTOP-ONLY version: no ESP32, no phone. Everything happens on this laptop,
with a small chat window that shows the whole conversation.

Select text with the mouse anywhere, then press (right after selecting):

  X    add the selection to the next question (shown in the window)
  G    add the selection and ASK the AI. The answer appears in the chat window
       and is typed where your text cursor is (3 s to click there first).
  M    NEW chat
  O    force "screen read": add what is on screen where you dragged (OCR)

When an answer is ready (and not typed yet):
  T    type it right now where your text cursor is. Click the spot, press T.

Any time:
  ` `  press ` twice quickly: open the window. Twice again: minimize it to the dot.
       (One ` still types a normal `.)

While an answer is being typed:
  Space  PAUSE typing. Press Space again to RESUME from the same spot (F9 still stops it).
         (Only while typing. The rest of the time Space is a normal space.)
       Only right after an answer: once you type anything else, T is a normal letter again.

Works any time:
  F9   STOP typing / cancel the AI request
  F7   switch AI: Gemini -> Claude (API key) -> Claude Code -> ...

"Claude Code" uses the same Claude Code as VS Code (your Claude account):
no API key needed.
  F8   pause / resume the keys above

In the chat window you can also just TYPE, like a normal chat:
  "that's wrong, it should be in C++"   -> the AI fixes its last answer.
The chat is remembered (even after closing the script) until you press
New chat. It is saved in chat_history.json next to this file.

API keys live in ai_keys.json next to this file (keep it private).
Stop: close the window, or Ctrl+C in the terminal.
"""
import asyncio
import ctypes
import json
import math
import os
import queue
import re
import shutil
import subprocess
import tempfile
import threading
import time
import tkinter as tk
from ctypes import wintypes

try:                                            # real pixels, so mouse == screenshot coordinates
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass

import anthropic
import pyperclip
import requests
import uiautomation as auto
from pynput import keyboard, mouse

try:
    import winsound
except Exception:
    winsound = None

try:
    from PIL import Image, ImageGrab
    from winrt.windows.graphics.imaging import BitmapAlphaMode, BitmapPixelFormat, SoftwareBitmap
    from winrt.windows.media.ocr import OcrEngine
    from winrt.windows.storage.streams import DataWriter
    OCR_OK = True
except Exception as e:                          # still works, just without the screen read
    print("Screen read (OCR) disabled:", e)
    OCR_OK = False


# =====================================================================
#  Settings
# =====================================================================
GEMINI_MODEL = "gemini-2.5-flash"
CLAUDE_MODELS = {"opus": "claude-opus-5-5", "sonnet": "claude-sonnet-5-5"}   # Claude with API key
CLAUDE_EFFORT = "low"              # low | medium | high | xhigh | max (higher = slower, smarter)
MODEL_NAMES = {"opus": "Opus", "sonnet": "Sonnet"}   # picked in the window (Claude Code + Claude)
LOCAL_TIMEOUT_S = 600              # give up on Claude Code after this long
PROVIDERS = ["gemini", "claude", "local"]
PROVIDER_NAMES = {"gemini": "Gemini", "claude": "Claude", "local": "Claude Code"}
SYSTEM_PROMPT = (  # only used if answer_rules.txt is missing
    "Answer concisely in plain text only. Use only standard ASCII characters. "
                 "No markdown, no bullet symbols, no code fences. "
                 "If the task asks for code, output only the code. "
                 "This is an ongoing chat: if the user says an earlier answer was wrong, "
                 "use the whole conversation to understand what they need and give the corrected answer.")
MAX_TURNS = 400                    # messages kept (question + answer = 2). Plenty for one chat.
CLEAN = True                       # strip markdown / non-ASCII from the answer

TYPE_ANSWERS = True                # G: also type the answer at the cursor (checkbox in the window)
OUTPUT_MODE = "type"               # "type" = key by key (works where paste is blocked)
                                   # "paste" = instant, keeps code indentation exact
WAIT_S = 3                         # seconds to click where the answer should go
CODE_EDITOR = True                 # typing into a code editor: beat autocomplete / auto-indent
SUGGEST_WAIT_S = 0.03              # code editor mode: wait this long for the popup before Esc
TYPE_DELAY_MS = 8                  # pause between typed characters (slider in Settings: 1-60)
PRESS_ENTER = False                # press Enter after the answer

VK_SEND, VK_CLEAR, VK_ASK, VK_OCR = 0x58, 0x4D, 0x47, 0x4F   # X, M, G, O
VK_TYPE = 0x54                                               # T
VK_PAUSE_TYPING = 0x20                                       # Space (only while typing)
VK_TOGGLE = 0xC0                                             # ` (the key left of 1)
DOUBLE_TAP_S = 0.35                # press ` twice within this time = open / minimize the window
NAV_VKS = {0x0D, 0x21, 0x22, 0x23, 0x24, 0x25, 0x26, 0x27, 0x28}  # Enter, PgUp/Dn, Home/End, arrows
VK_STOP, VK_PAUSE, VK_SWITCH = 0x78, 0x77, 0x76              # F9, F8, F7
DRAG_PX = 8                        # movement that counts as a drag-select
MULTI_CLICK_S = 0.45               # double / triple-click window
ARMED_FOR_S = 30                   # keys work this long after a selection
ARMED_AFTER_KEY_S = 12             # after X / M / O, the keys stay active this much longer
MAX_CHARS = 20000
SEPARATOR = "\n"                   # placed between added selections
TERMINALS = ("ConsoleWindowClass", "CASCADIA_HOSTING_WINDOW_CLASS")
OCR_PAD = 40                       # px above/below the drag that is read from the screen
OCR_SCALE = 2                      # enlarge the screenshot first: small text reads better

HERE = os.path.dirname(os.path.abspath(__file__))
KEYS_FILE = os.path.join(HERE, "ai_keys.json")
CHAT_FILE = os.path.join(HERE, "chat_history.json")
RULES_FILE = os.path.join(HERE, "answer_rules.txt")   # how the AI should answer (edit freely)


def system_prompt():
    """The answer rules. Read fresh each time, so edits apply without a restart."""
    try:
        with open(RULES_FILE, encoding="utf-8") as f:
            rules = f.read().strip()
        if rules:
            return rules
    except Exception:
        pass
    return SYSTEM_PROMPT

user32 = ctypes.windll.user32
user32.GetForegroundWindow.restype = wintypes.HWND
user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
kb = keyboard.Controller()
jobs = queue.Queue()                # work for the background thread
ui_q = queue.Queue()                # messages for the window
stop_event = threading.Event()
state = {
    "enabled": True,
    "type_answers": TYPE_ANSWERS,
    "buffer": "",                  # selected text waiting to be asked
    "last_piece": "",
    "history": [],                 # [{role, text, raw, prov}]
    "last_answer": "",
    "answer_ready": False,         # an answer is waiting: T types it
    "output_mode": OUTPUT_MODE,    # "type" or "paste" (changeable in the window)
    "code_editor": CODE_EDITOR,    # code editor mode (checkbox in Settings)
    "type_delay_ms": TYPE_DELAY_MS,  # typing speed, read for every key so the slider works live
    "ext_hwnd": None,              # the last other app you used (buttons go back to it)
    "click_ours": False,
    "typing": False,               # an answer is being typed right now
    "typing_paused": False,        # ... and Space paused it
    "paused_at": 0.0,
    "dot_hwnd": None,              # the small dot shown when the window is minimized
    "gen": 0,                      # bumped by F9: a reply from an older gen is thrown away
    "ui_hwnd": None,
    "press": (0, 0),
    "last_up": 0.0,
    "clicks": 0,
    "sel": None,
    "armed_at": 0.0,
    "last_key": 0.0,
}
MODIFIER_VKS = {0x10, 0x11, 0x12, 0xA0, 0xA1, 0xA2, 0xA3, 0xA4, 0xA5, 0x5B, 0x5C}


# =====================================================================
#  Talking to the window (it runs in the main thread)
# =====================================================================
def log(msg, tone=None):
    """Status line in the window + terminal. tone: good / warn / err (guessed if not given)."""
    print(msg)
    if tone is None:
        low = msg.lower()
        if low.startswith(("asking", "typing")):
            tone = "warn"
        elif any(w in low for w in ("replied", "typed", "added", "new chat", "ready", "now ")):
            tone = "good"
        else:
            tone = ""
    ui_q.put(("status", msg, tone))


def set_ready(v):
    """An answer is waiting to be typed with T (the Type button lights up)."""
    if state["answer_ready"] != v:
        state["answer_ready"] = v
        ui("ready", v)


def toggle_pause():
    state["enabled"] = not state["enabled"]
    ui("paused", not state["enabled"])
    log("Keys: " + ("ON" if state["enabled"] else "PAUSED (F8 or the button to resume)"),
        "good" if state["enabled"] else "warn")


def ui(kind, *args):
    ui_q.put((kind,) + args)


# =====================================================================
#  API keys / saved settings / saved chat
# =====================================================================
def load_settings():
    try:
        with open(KEYS_FILE, encoding="utf-8") as f:
            c = json.load(f)
    except Exception:
        c = {}
    c.setdefault("provider", "gemini")
    c.setdefault("gemini_key", "")
    c.setdefault("claude_key", "")
    c.setdefault("local_session", "")            # Claude Code conversation id
    c.setdefault("local_synced", 0)              # how many chat messages that conversation has seen
    if c.get("model") not in MODEL_NAMES:
        c["model"] = "opus"                      # Opus or Sonnet for Claude / Claude Code
    if c["provider"] not in PROVIDERS:
        c["provider"] = "gemini"
    if os.environ.get("GEMINI_API_KEY"):
        c["gemini_key"] = os.environ["GEMINI_API_KEY"]
    if os.environ.get("ANTHROPIC_API_KEY"):
        c["claude_key"] = os.environ["ANTHROPIC_API_KEY"]
    return c


def save_settings():
    try:
        with open(KEYS_FILE, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2)
    except Exception as e:
        print("Could not save settings:", e)


def load_chat():
    try:
        with open(CHAT_FILE, encoding="utf-8") as f:
            h = json.load(f)
        return h if isinstance(h, list) else []
    except Exception:
        return []


def save_chat():
    try:
        tmp = CHAT_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(state["history"], f, indent=1)
        os.replace(tmp, CHAT_FILE)
    except Exception as e:
        print("Could not save the chat:", e)


def provider_name(p=None):
    return PROVIDER_NAMES.get(p or cfg["provider"], "Gemini")


def answer_label(prov, model=None):
    name = provider_name(prov)
    if prov in ("claude", "local") and model in MODEL_NAMES:
        name += "  -  " + MODEL_NAMES[model]
    return name


def key_for(p=None):
    p = p or cfg["provider"]
    if p == "local":
        return "local"                           # uses your Claude Code login, no key
    return cfg["claude_key" if p == "claude" else "gemini_key"].strip()


cfg = load_settings()
state["history"] = load_chat()


# =====================================================================
#  Small helpers
# =====================================================================
def held(vk):
    return (user32.GetAsyncKeyState(vk) & 0x8000) != 0


def any_modifier():
    return held(0x10) or held(0x11) or held(0x12) or held(0x5B) or held(0x5C)


def foreground_class():
    buf = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(user32.GetForegroundWindow(), buf, 256)
    return buf.value


def in_our_window():
    h = user32.GetForegroundWindow()
    return bool(h) and h == state["ui_hwnd"]


def armed():
    return state["armed_at"] and (time.time() - state["armed_at"]) < ARMED_FOR_S


def shorten_armed():
    state["armed_at"] = min(state["armed_at"], time.time() - (ARMED_FOR_S - ARMED_AFTER_KEY_S))


def beep(freq=880, ms=120):
    if winsound:
        try:
            winsound.Beep(freq, ms)
        except Exception:
            pass


def preview(s, n=60):
    s = " ".join(s.split())
    return s if len(s) <= n else s[:n - 3] + "..."


# =====================================================================
#  Reading the selection
# =====================================================================
# ---------- Method 1: direct read ----------
def read_direct():
    try:
        with auto.UIAutomationInitializerInThread():
            ctrl = auto.GetFocusedControl()
            for _ in range(4):                   # browsers expose text on a parent
                if ctrl is None:
                    break
                try:
                    pattern = ctrl.GetTextPattern()
                except Exception:
                    pattern = None
                if pattern:
                    text = "".join(r.GetText(MAX_CHARS) for r in pattern.GetSelection())
                    if text:
                        return text
                ctrl = ctrl.GetParentControl()
    except Exception:
        pass
    return None


# ---------- Method 2: script copies the selection ----------
def read_by_copy():
    if foreground_class() in TERMINALS:          # Ctrl+C would interrupt a running program
        return None
    try:
        old = pyperclip.paste()
    except Exception:
        old = None
    try:
        pyperclip.copy("")
    except Exception:
        return None

    time.sleep(0.05)
    with kb.pressed(keyboard.Key.ctrl):
        kb.press("c")
        kb.release("c")

    text = ""
    for _ in range(12):                          # wait up to ~0.6 s for the copy
        time.sleep(0.05)
        try:
            text = pyperclip.paste()
        except Exception:
            text = ""
        if text:
            break

    if old is not None:
        try:
            pyperclip.copy(old)                  # put the previous clipboard back
        except Exception:
            pass
    return text or None


# ---------- Method 3: read the text off the screen (OCR) ----------
def foreground_rect():
    r = wintypes.RECT()
    user32.GetWindowRect(user32.GetForegroundWindow(), ctypes.byref(r))
    return r.left, r.top, r.right, r.bottom


async def _recognize(img):
    w, h = img.size
    dw = DataWriter()
    dw.write_bytes(img.convert("RGBA").tobytes("raw", "BGRA"))
    bmp = SoftwareBitmap.create_copy_from_buffer(
        dw.detach_buffer(), BitmapPixelFormat.BGRA8, w, h, BitmapAlphaMode.PREMULTIPLIED)
    engine = OcrEngine.try_create_from_user_profile_languages()
    if engine is None:
        return None
    return await engine.recognize_async(bmp)


def ocr_lines(left, top, right, bottom):
    """Lines top to bottom; each line is a list of (text, x0, y0, x1, y1) in screen pixels."""
    img = ImageGrab.grab(bbox=(left, top, right, bottom), all_screens=True)
    scale = OCR_SCALE if max(img.size) * OCR_SCALE <= 9000 else 1
    if scale != 1:
        img = img.resize((img.width * scale, img.height * scale), Image.LANCZOS)
    res = asyncio.run(_recognize(img))
    if res is None:
        return []
    lines = []
    for line in res.lines:
        words = []
        for wd in line.words:
            r = wd.bounding_rect
            words.append((wd.text, left + r.x / scale, top + r.y / scale,
                          left + (r.x + r.width) / scale, top + (r.y + r.height) / scale))
        if words:
            lines.append(words)
    lines.sort(key=lambda ws: min(w[2] for w in ws))
    return lines


def _box(ws):
    return min(w[1] for w in ws), min(w[2] for w in ws), max(w[3] for w in ws), max(w[4] for w in ws)


def _nearest_line(lines, x, y):
    def dist(ws):
        x0, y0, x1, y1 = _box(ws)
        dy = 0 if y0 <= y <= y1 else min(abs(y - y0), abs(y - y1))
        dx = 0 if x0 <= x <= x1 else min(abs(x - x0), abs(x - x1))
        return dy, dx
    return min(range(len(lines)), key=lambda i: dist(lines[i]))


def _join(ws):
    return " ".join(w[0] for w in ws)


def read_by_ocr(sel):
    if not OCR_OK or not sel:
        return None
    try:
        x1, y1, x2, y2, clicks = sel
        wl, wt, wr, wb = foreground_rect()
        if wr - wl < 50 or wb - wt < 20:
            wl, wr, wt, wb = min(x1, x2) - 1500, max(x1, x2) + 1500, -100000, 100000
        top = max(wt, min(y1, y2) - OCR_PAD)
        bottom = min(wb, max(y1, y2) + OCR_PAD)
        lines = ocr_lines(wl, top, wr, bottom)
        if not lines:
            return None

        if clicks >= 3:                          # triple-click: the whole line
            return _join(lines[_nearest_line(lines, x2, y2)])
        if clicks == 2:                          # double-click: the word under the mouse
            ws = lines[_nearest_line(lines, x2, y2)]
            return min(ws, key=lambda w: 0 if w[1] <= x2 <= w[3] else min(abs(x2 - w[1]), abs(x2 - w[3])))[0]

        a, b = (x1, y1), (x2, y2)                # drag, in reading order
        ia, ib = _nearest_line(lines, *a), _nearest_line(lines, *b)
        if ia > ib or (ia == ib and a[0] > b[0]):
            a, b, ia, ib = b, a, ib, ia
        if ia == ib:
            return _join([w for w in lines[ia] if w[3] > a[0] and w[1] < b[0]])

        col_l = min(_box(lines[ib])[0], a[0], b[0]) - 20
        col_r = max(_box(lines[ia])[2], a[0], b[0]) + 20
        out = [_join([w for w in lines[ia] if w[3] > a[0]])]
        for i in range(ia + 1, ib):
            l0, _, r0, _ = _box(lines[i])
            if r0 > col_l and l0 < col_r:
                out.append(_join([w for w in lines[i] if w[3] > col_l and w[1] < col_r]))
        out.append(_join([w for w in lines[ib] if w[1] < b[0]]))
        return "\n".join(s for s in out if s)
    except Exception as e:
        print("Screen read failed:", e)
        return None


def get_selection(fallback, force_ocr=False):
    """Try every method in turn. Returns (text, how)."""
    sel = state["sel"]
    if not force_ocr:
        text = read_direct()
        if text and text.strip():
            return text, "direct read"
        if fallback:
            text = read_by_copy()
            if text and text.strip():
                return text, "copied"
    if fallback or force_ocr:
        text = read_by_ocr(sel)
        if text and text.strip():
            return text, "screen read"
    return None, ""


# =====================================================================
#  The AI
# =====================================================================
_claude_client = {"key": None, "client": None}


def claude_client():
    key = key_for("claude")
    if _claude_client["key"] != key:
        _claude_client["client"] = anthropic.Anthropic(api_key=key, timeout=180.0)
        _claude_client["key"] = key
    return _claude_client["client"]


def ask_claude(turns):
    msgs = []
    for t in turns:
        # Claude's own earlier replies go back exactly as they came; anything else as text.
        use_raw = t["role"] == "assistant" and t.get("raw") and t.get("prov") == "claude"
        msgs.append({"role": t["role"], "content": t["raw"] if use_raw else t["text"]})
    params = dict(
        model=CLAUDE_MODELS[cfg["model"]],
        max_tokens=16000,
        system=system_prompt(),
        messages=msgs,
        output_config={"effort": CLAUDE_EFFORT},
    )
    client = claude_client()
    if re.match(r"^claude-(opus-5|sonnet-5-5|fable-5)", params["model"]):
        # If this model declines a request, Anthropic retries it on another model automatically.
        resp = client.beta.messages.create(
            betas=["server-side-fallback-2026-07-01"], fallbacks="default", **params)
    else:
        resp = client.messages.create(**params)

    if resp.stop_reason == "refusal":
        why = getattr(resp.stop_details, "explanation", None) if resp.stop_details else None
        raise RuntimeError("Claude declined this request" + (": " + why if why else "."))
    text = "".join(b.text for b in resp.content if b.type == "text")
    if not text:
        raise RuntimeError("Claude returned no text.")
    if resp.stop_reason == "max_tokens":
        text += "\n[reply cut off]"
    return text, [b.to_dict(mode="json") for b in resp.content]


def ask_gemini(turns):
    body = {"contents": [{"role": "model" if t["role"] == "assistant" else "user",
                          "parts": [{"text": t["text"]}]} for t in turns]}
    body["systemInstruction"] = {"parts": [{"text": system_prompt()}]}
    r = requests.post(
        f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent",
        headers={"Content-Type": "application/json", "x-goog-api-key": key_for("gemini")},
        json=body, timeout=180)
    try:
        j = r.json()
    except Exception:
        j = {}
    if not r.ok:
        raise RuntimeError((j.get("error") or {}).get("message") or f"Gemini error {r.status_code}")
    cands = j.get("candidates") or []
    parts = ((cands[0].get("content") or {}).get("parts") or []) if cands else []
    text = "".join(p.get("text", "") for p in parts)
    if not text:
        block = (j.get("promptFeedback") or {}).get("blockReason")
        raise RuntimeError("Gemini returned no text" + (f" (blocked: {block})" if block else "."))
    return text, None


LOCAL_DIR = os.path.join(tempfile.gettempdir(), "laptop_ai_claude")   # empty folder for Claude Code
_local_proc = {"p": None}


def ask_local(turns):
    """Ask the Claude Code on this laptop (your Claude login). It keeps its own
    conversation, so normally only the new message is sent."""
    exe = shutil.which("claude")
    if not exe:
        raise RuntimeError("Claude Code not found. Is 'claude' installed and on PATH?")
    os.makedirs(LOCAL_DIR, exist_ok=True)

    history, q = turns[:-1], turns[-1]["text"]
    sid = cfg.get("local_session", "")
    if sid and cfg.get("local_synced", 0) == len(history):
        prompt = q                               # Claude Code already knows the chat
    else:
        # New conversation, or other AIs answered in between: send the chat so far.
        sid = ""
        if history:
            lines = [("User: " if t["role"] == "user" else "Assistant: ") + t["text"] for t in history]
            prompt = ("Our conversation so far:\n\n" + "\n\n".join(lines)
                      + "\n\n---\nMy new message:\n" + q)
        else:
            prompt = q

    cmd = [exe, "-p", "--output-format", "json", "--tools", "", "--system-prompt", system_prompt()]
    cmd += ["--model", cfg["model"]]             # "opus" or "sonnet"
    if sid:
        cmd += ["--resume", sid]

    p = subprocess.Popen(cmd, cwd=LOCAL_DIR, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                         stderr=subprocess.PIPE, encoding="utf-8", errors="replace",
                         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    _local_proc["p"] = p
    try:
        out, err = p.communicate(prompt, timeout=LOCAL_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        p.kill()
        raise RuntimeError(f"no answer after {LOCAL_TIMEOUT_S} s.")
    finally:
        _local_proc["p"] = None

    if stop_event.is_set():
        raise RuntimeError("stopped.")
    try:
        d = json.loads(out)
    except Exception:
        raise RuntimeError((err.strip() or out.strip())[-300:]
                           or f"Claude Code gave no answer (exit code {p.returncode}).")
    if d.get("is_error"):
        if sid:                                  # the saved conversation may be gone: start fresh next time
            cfg["local_session"], cfg["local_synced"] = "", 0
            save_settings()
        raise RuntimeError(str(d.get("result") or d.get("subtype") or "Claude Code error")[:300])
    text = (d.get("result") or "").strip()
    if not text:
        raise RuntimeError("Claude Code returned no text.")
    cfg["local_session"] = d.get("session_id") or sid
    cfg["local_synced"] = len(turns) + 1         # history + this question + this answer
    save_settings()
    return text, None


def clean(s):
    s = re.sub(r"```[a-zA-Z0-9_+-]*\n?", "", s)
    # (no "**" stripping: it would break x ** 2 and **kwargs)
    # (no "#" stripping: it would break #include, # comments, etc.)
    for a, b in (("‘", "'"), ("’", "'"), ("“", '"'), ("”", '"'),
                 ("–", "-"), ("—", "-"), ("…", "..."), (" ", " "), ("•", "-")):
        s = s.replace(a, b)
    s = re.sub(r"[^\x09\x0A\x0D\x20-\x7E]", "", s)
    return s.strip()


def trim_history():
    h = state["history"]
    if len(h) > MAX_TURNS:
        del h[:len(h) - MAX_TURNS]
    while h and h[0]["role"] != "user":
        h.pop(0)


# =====================================================================
#  Typing the answer at the cursor
# =====================================================================
def _tap(key, *mods):
    for m in mods:
        kb.press(m)
    kb.press(key)
    kb.release(key)
    for m in reversed(mods):
        kb.release(m)


def _is_word(ch):
    return ch.isalnum() or ch == "_"


def _close_suggestions():
    """Code editors pop up autocomplete after a word; the next ; ( . or Enter would
    accept it (cin >> B; became cin >> Bbits). Moving the cursor Left and back Right
    closes the popup and leaves the cursor where it was. (No Esc: in Chrome, Esc
    leaves full screen and can close dialogs on the page.)"""
    time.sleep(SUGGEST_WAIT_S)                   # give the popup time to appear, then close it
    _tap(keyboard.Key.left)
    _tap(keyboard.Key.right)


def pause_typing(msg="Typing PAUSED.  Space (or Resume typing) to continue,  F9 = stop."):
    if state["typing"] and not state["typing_paused"]:
        state["typing_paused"] = True
        state["paused_at"] = time.time()
        ui("typing_paused", True)
        log(msg, "warn")


def resume_typing(from_button=False):
    """Continue typing. From the button: go back to the app you were typing into first."""
    if not state["typing_paused"]:
        return
    def go():
        if from_button:
            focus_ext()
        state["typing_paused"] = False
        ui("typing_paused", False)
        log("Typing resumed.  Space = pause,  F9 = stop.", "warn")
    if from_button:
        threading.Thread(target=go, daemon=True).start()
    else:
        go()


def toggle_typing_pause(from_button=False):
    if not state["typing"]:
        return
    if state["typing_paused"]:
        if from_button and time.time() - state["paused_at"] < 0.6:
            return                               # this same click just paused it (see on_click)
        resume_typing(from_button)
    else:
        pause_typing()


def wait_if_paused():
    """Called before every key. Holds while paused. False = stopped with F9."""
    if not state["typing_paused"] and in_our_window():
        pause_typing("Typing paused: the chat window is in front. Click where it was typing, "
                     "then press Space (or Resume typing).")
    while state["typing_paused"]:
        if stop_event.wait(0.1):
            return False
    # A Ctrl / Alt / Win you are holding would turn the next typed letter into a shortcut
    # (Ctrl+D, Ctrl+Alt+Up/Down, Alt+click = an extra cursor in VS Code / Monaco editors).
    # Our own taps always release their modifiers, so anything held here is yours: wait.
    warned = False
    while held(0x11) or held(0x12) or held(0x5B) or held(0x5C):
        if not warned:
            log("Typing waits: let go of Ctrl / Alt / Win.", "warn")
            warned = True
        if stop_event.wait(0.05):
            return False
    if warned:
        time.sleep(0.05)
        log("Typing...  (F9 = stop)")
    return not stop_event.is_set()


def type_text(text):
    """Type key by key. Returns False if stopped with F9."""
    code = state["code_editor"]
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    done, total = 0, len(text)
    for li, line in enumerate(lines):
        if li > 0:
            if not wait_if_paused():
                log(f"Stopped after {done} of {total} characters.")
                return False
            if code:
                _close_suggestions()
                _tap(keyboard.Key.end, keyboard.Key.shift)   # select what the editor added after the
            _tap(keyboard.Key.enter)                          # cursor (an auto-closed }): Enter replaces it
            if code:
                time.sleep(0.02)
                _tap(keyboard.Key.home, keyboard.Key.shift)  # select the editor's auto-indent:
            done += 1                                         # our own spaces replace it
        prev_word = False
        for ch in line:
            if not wait_if_paused():
                log(f"Stopped after {done} of {total} characters.")
                return False
            if code and prev_word and not _is_word(ch):
                _close_suggestions()
            if ch == "\t":
                _tap(keyboard.Key.tab)
            else:
                kb.type(ch)
            prev_word = _is_word(ch)
            done += 1
            time.sleep(state["type_delay_ms"] / 1000)
    if code:
        _close_suggestions()
    return True


def output(text, wait=WAIT_S):
    state["typing"], state["typing_paused"] = True, False
    ui("typing", True)
    try:
        _output(text, wait)
    finally:
        state["typing"], state["typing_paused"] = False, False
        ui("typing", False)


def _output(text, wait=WAIT_S):
    """Wait a moment so you can click where it should go, then type or paste it."""
    stop_event.clear()
    beep(880)
    set_ready(False)
    if not wait:
        time.sleep(0.15)                         # let go of the T key first
    for i in range(wait, 0, -1):
        log(f"Typing in {i}...  click where the answer should go  (F9 = cancel)")
        if stop_event.wait(1):
            log("Stopped.")
            return
    if in_our_window():
        log("Not typed: the chat window was in front. Click the target first, then press T.")
        return

    if state["output_mode"] == "paste":
        try:
            old = pyperclip.paste()
        except Exception:
            old = None
        pyperclip.copy(text)
        time.sleep(0.05)
        with kb.pressed(keyboard.Key.ctrl):
            kb.press("v")
            kb.release("v")
        time.sleep(0.4)                          # let the app read the clipboard first
        if old is not None:
            try:
                pyperclip.copy(old)
            except Exception:
                pass
    else:
        log("Typing...  (F9 = stop)")
        if not type_text(text):
            return

    if PRESS_ENTER and not stop_event.is_set():
        kb.press(keyboard.Key.enter)
        kb.release(keyboard.Key.enter)
    beep(660, 80)
    log(f"Typed {len(text)} characters.")


# =====================================================================
#  Actions (run in the background thread)
# =====================================================================
def set_buffer(text):
    state["buffer"] = text
    ui("pending", text)


def do_add(fallback=True, force_ocr=False):
    """Add the current selection to the next question."""
    text, how = get_selection(fallback, force_ocr)
    if not text:
        log("No selection found.")
        return
    if text == state["last_piece"]:
        log("Same text as the last one, not added twice.")
        return
    set_buffer((state["buffer"] + SEPARATOR + text if state["buffer"] else text)[:MAX_CHARS])
    state["last_piece"] = text
    log(f"Added ({how}): {preview(text)}")


def ask(extra="", type_out=False):
    """Ask the AI: the selected text waiting + what was typed in the window."""
    stop_event.clear()
    my_gen = state["gen"]
    q = "\n\n".join(s for s in (state["buffer"].strip(), extra.strip()) if s)
    if not q:
        log("Nothing to ask yet. Select text and press X or G, or type in the box.")
        return
    prov = cfg["provider"]
    name = provider_name(prov)
    used_model = cfg["model"] if prov in ("claude", "local") else None
    if used_model:
        name = answer_label(prov, used_model)
    if not key_for(prov):
        log(f"No {name} API key. Add it to ai_keys.json (or press F7 to switch AI).")
        return

    set_buffer("")
    state["last_piece"] = ""
    ui("msg", "user", q, None)
    turns = state["history"] + [{"role": "user", "text": q}]
    log(f"Asking {name}...")
    ui("busy", True)
    t0 = time.time()
    try:
        fn = {"claude": ask_claude, "local": ask_local}.get(prov, ask_gemini)
        text, raw = fn(turns)
    except anthropic.AuthenticationError:
        return fail(q, "Claude: the API key is wrong. Fix claude_key in ai_keys.json.")
    except anthropic.RateLimitError:
        return fail(q, "Claude: too many requests or out of credit. Wait a bit and try again.")
    except anthropic.APIConnectionError:
        return fail(q, "Claude: no internet connection.")
    except anthropic.APIStatusError as e:
        return fail(q, f"Claude error {e.status_code}: {e.message}")
    except requests.RequestException as e:
        return fail(q, f"{name}: network problem ({e.__class__.__name__}).")
    except Exception as e:
        return fail(q, f"{name}: {e}")
    finally:
        ui("busy", False)

    if state["gen"] != my_gen:                   # F9 was pressed while waiting
        return fail(q, "Cancelled.")

    answer = clean(text) if CLEAN else text
    state["history"] += [{"role": "user", "text": q},
                         {"role": "assistant", "text": answer, "raw": raw, "prov": prov,
                          "model": used_model}]
    trim_history()
    save_chat()
    state["last_answer"] = answer
    ui("msg", "assistant", answer, answer_label(prov, used_model))
    if type_out:
        log(f"{name} replied in {time.time() - t0:.1f}s.")
        output(answer)
    else:
        set_ready(True)
        log(f"{name} replied in {time.time() - t0:.1f}s.  Click where it should go and press T to type it.")


def fail(q, msg):
    """The question did not get an answer: give it back so it can be asked again."""
    ui("msg", "error", msg, None)
    log(msg, "err")
    if not state["buffer"]:
        set_buffer(q)                            # nothing lost: press Send or G to retry


def do_newchat():
    state["history"] = []
    state["last_answer"] = ""
    state["last_piece"] = ""
    set_buffer("")
    save_chat()
    cfg["local_session"], cfg["local_synced"] = "", 0
    save_settings()
    ui("clear")
    log("New chat started. The AI forgot everything before this.")


def do_stop():
    state["gen"] += 1
    stop_event.set()
    p = _local_proc["p"]
    if p and p.poll() is None:                   # cancel a waiting Claude Code question
        try:
            p.kill()
        except Exception:
            pass
    log("STOPPED.", "err")


def do_switch(to=None):
    if to is None:
        i = PROVIDERS.index(cfg["provider"]) if cfg["provider"] in PROVIDERS else 0
        to = PROVIDERS[(i + 1) % len(PROVIDERS)]
    cfg["provider"] = to
    save_settings()
    ui("provider", cfg["provider"])
    note = "" if key_for() else "  (no key yet: add it to ai_keys.json)"
    log(f"AI is now {provider_name()}.{note}")


user32.WindowFromPoint.argtypes = [wintypes.POINT]
user32.WindowFromPoint.restype = wintypes.HWND
user32.GetAncestor.argtypes = [wintypes.HWND, ctypes.c_uint]
user32.GetAncestor.restype = wintypes.HWND
user32.SetForegroundWindow.argtypes = [wintypes.HWND]
user32.IsWindow.argtypes = [wintypes.HWND]


def point_in_ours(x, y):
    try:
        h = user32.WindowFromPoint(wintypes.POINT(int(x), int(y)))
        r = user32.GetAncestor(h, 2) if h else None                     # 2 = GA_ROOT
        return bool(r) and r in (state["ui_hwnd"], state["dot_hwnd"])
    except Exception:
        return False


def focus_ext():
    """Buttons are clicked in this window: switch back to the app you were using."""
    h = state["ext_hwnd"]
    if not h or not user32.IsWindow(h):
        log("Use the button after selecting text in another app.", "warn")
        return False
    user32.SetForegroundWindow(h)
    time.sleep(0.25)
    return True


def do_button(what):
    if what in ("send", "ask", "ocr", "type") and not focus_ext():
        return
    if what == "send":
        do_add(True)
    elif what == "ask":
        do_add(True)
        ask(type_out=state["type_answers"])
    elif what == "ocr":
        do_add(True, force_ocr=True)
    elif what == "type":
        if state["last_answer"]:
            output(state["last_answer"], wait=0)
        else:
            log("No answer to type yet.", "warn")


def do_model(m):
    cfg["model"] = m
    save_settings()
    ui("model", m)
    log(f"Claude model: {MODEL_NAMES[m]}. Used by Claude Code and Claude.", "good")


def worker():
    while True:
        job = jobs.get()
        time.sleep(0.05)
        try:
            kind = job[0]
            if kind == "send":
                do_add(job[1])
            elif kind == "ocr":
                do_add(job[1], force_ocr=True)
            elif kind == "ask":                  # G after a selection
                do_add(job[1])
                ask(type_out=state["type_answers"])
            elif kind == "chat":                 # typed in the window
                ask(job[1], type_out=False)
            elif kind == "type_now":             # T key: type right away
                if state["last_answer"]:
                    output(state["last_answer"], wait=0)
            elif kind == "type_last":
                if state["last_answer"]:
                    output(state["last_answer"])
                else:
                    log("No answer to type yet.")
            elif kind == "model":
                do_model(job[1])
            elif kind == "btn":                  # a button in the window
                do_button(job[1])
            elif kind == "newchat":
                do_newchat()
            elif kind == "switch":
                do_switch(job[1] if len(job) > 1 else None)
        except Exception as e:
            log(f"Error: {e}")


# =====================================================================
#  Mouse and keyboard hooks
# =====================================================================
def on_click(x, y, button, pressed):
    if button != mouse.Button.left:
        return
    now = time.time()
    if pressed:
        state["click_ours"] = point_in_ours(x, y)
        if state["click_ours"] and state["typing"]:
            pause_typing("Typing paused (you clicked the chat window).  "
                         "Resume typing or Space to continue.")
        if not state["click_ours"]:
            state["press"] = (x, y)
            if state["typing"] and not state["typing_paused"]:
                # A click in the target app moves the caret (Alt+click even adds a second
                # cursor in code editors) while keys are still being sent: stop and wait.
                pause_typing("Typing paused (you clicked).  Click where it should continue, "
                             "then Space to resume.  F9 = stop.")
        return
    if state["click_ours"]:                      # clicks in the chat window keep the selection info
        return
    fg = user32.GetForegroundWindow()
    if fg and fg not in (state["ui_hwnd"], state["dot_hwnd"]):
        state["ext_hwnd"] = fg
    px, py = state["press"]
    dragged = math.hypot(x - px, y - py) > DRAG_PX
    multi = (now - state["last_up"]) < MULTI_CLICK_S
    state["last_up"] = now
    state["clicks"] = state["clicks"] + 1 if multi else 1
    if dragged:
        state["sel"] = (px, py, x, y, 1)
    elif multi:
        state["sel"] = (x, y, x, y, state["clicks"])
    else:
        state["sel"] = None
    state["armed_at"] = now if (dragged or multi) else 0.0


def on_scroll(x, y, dx, dy):
    state["sel"] = None                          # the text moved: the screen spot is stale


_tick = {"timer": None, "down": False}


def _type_held_backtick():
    """A single ` was not followed by a second one: type it after all."""
    _tick["timer"] = None
    kb.type("`")


def backtick(msg, data):
    """` pressed twice quickly opens / minimizes the window. One ` still types a `."""
    if msg in (0x101, 0x105):                    # key up
        _tick["down"] = False
        return
    if _tick["down"]:                            # key held down (auto-repeat): ignore
        listener.suppress_event()
        return
    _tick["down"] = True
    if any_modifier():
        return                                   # Shift+` (~) and others: leave alone
    if _tick["timer"] is not None:               # second ` in time: toggle the window
        _tick["timer"].cancel()
        _tick["timer"] = None
        ui("toggle_window")
    else:                                        # first `: hold it back for a moment
        t = threading.Timer(DOUBLE_TAP_S, _type_held_backtick)
        t.daemon = True
        _tick["timer"] = t
        t.start()
    listener.suppress_event()


_space = {"down": False}


def key_filter(msg, data):
    """Runs before Windows delivers each key. Keep it fast."""
    if data.flags & 0x10:                        # keys typed by this script itself
        return
    if data.vkCode == VK_TOGGLE:
        backtick(msg, data)
        return
    if data.vkCode == VK_PAUSE_TYPING and msg in (0x101, 0x105):
        was_ours = _space["down"]
        _space["down"] = False
        if was_ours:
            listener.suppress_event()            # its key-down was the pause key: hide the key-up too
        return
    if msg not in (0x100, 0x104):                # key down / sys key down
        return

    vk = data.vkCode
    now = time.time()

    if vk == VK_PAUSE_TYPING and state["typing"]:   # Space: pause / resume typing
        if not _space["down"]:
            _space["down"] = True
            if not any_modifier():
                toggle_typing_pause()
        listener.suppress_event()
        return
    if vk == VK_STOP:                            # works any time, even while paused
        do_stop()
        listener.suppress_event()
        return
    if vk == VK_PAUSE:
        toggle_pause()
        return
    if in_our_window():                          # typing in the chat box: leave keys alone
        return
    if vk == VK_TYPE and state["enabled"] and state["answer_ready"] and not any_modifier():
        set_ready(False)
        jobs.put(("type_now",))
        listener.suppress_event()
        return
    if vk == VK_SWITCH and not any_modifier():
        jobs.put(("switch",))
        listener.suppress_event()
        return

    if vk in (VK_SEND, VK_ASK, VK_CLEAR, VK_OCR) and state["enabled"] and not any_modifier():
        if vk == VK_SEND:
            if armed():
                if now - state["last_key"] > 0.3:    # ignore key auto-repeat
                    state["last_key"] = now
                    jobs.put(("send", True))
                    shorten_armed()
                listener.suppress_event()
            else:
                text = read_direct()
                if text and text.strip():
                    if now - state["last_key"] > 0.3:
                        state["last_key"] = now
                        jobs.put(("send", False))
                    listener.suppress_event()
            return

        if armed():                              # G, M and O only right after a selection
            if now - state["last_key"] > 0.3:
                state["last_key"] = now
                if vk == VK_ASK:
                    jobs.put(("ask", True))
                    state["armed_at"] = 0.0      # disarm: the answer will be typed next
                elif vk == VK_OCR:
                    jobs.put(("ocr", True))
                    shorten_armed()
                else:
                    jobs.put(("newchat",))
                    shorten_armed()
            listener.suppress_event()
            return

    if vk not in MODIFIER_VKS and vk not in NAV_VKS:
        set_ready(False)                         # you typed something: T is a letter again
    if vk not in MODIFIER_VKS:
        state["armed_at"] = 0.0                  # you typed something: disarm


listener = keyboard.Listener(win32_event_filter=key_filter)


# =====================================================================
#  The chat window (same look as the phone page: black, white, red STOP)
# =====================================================================
from tkinter import ttk

ALL_WHITE_BUTTONS = True           # True: every button plain white (False: Ask AI black, Type green...)
THEME = "light"                    # "light" (white) or "dark" (black, like the phone page)
PALETTES = {
    "light": dict(BG="#ffffff", BORDER="#d9d9de", FG="#111111", MUTED="#6b7280", DIM="#a1a1aa",
                  GOOD="#16a34a", WARN="#b45309", BAD="#dc2626", YOU_C="#2563eb", AI_C="#16a34a",
                  HOVER="#f3f4f6", MENU_ACTIVE="#e5e7eb", SCROLL="#d1d5db", SCROLL_ACTIVE="#9ca3af",
                  TROUGH="#e5e7eb", PENDING_BG="#eff6ff", CODE_BG="#f6f8fa",
                  PRI=("#111111", "#ffffff", "#a1a1aa", "#111111", "#333333"),
                  STOP_KEY="#fecaca", STOP_HOVER="#b91c1c", ON_BG="#ecfdf5", ON_HOVER="#d1fae5"),
    "dark": dict(BG="#000000", BORDER="#333333", FG="#ffffff", MUTED="#888888", DIM="#555555",
                 GOOD="#2ecc71", WARN="#f1c40f", BAD="#e74c3c", YOU_C="#7cb7ff", AI_C="#2ecc71",
                 HOVER="#151515", MENU_ACTIVE="#222222", SCROLL="#222222", SCROLL_ACTIVE="#444444",
                 TROUGH="#333333", PENDING_BG="#06121f", CODE_BG="#0b0b0b",
                 PRI=("#ffffff", "#000000", "#666666", "#ffffff", "#dddddd"),
                 STOP_KEY="#ffd5d0", STOP_HOVER="#c0392b", ON_BG="#0f2a1a", ON_HOVER="#143822"),
}
_P = PALETTES[THEME]
BG, BORDER, FG, MUTED, DIM = _P["BG"], _P["BORDER"], _P["FG"], _P["MUTED"], _P["DIM"]
GOOD, WARN, BAD = _P["GOOD"], _P["WARN"], _P["BAD"]
YOU_C, AI_C = _P["YOU_C"], _P["AI_C"]
HOVER, MENU_ACTIVE = _P["HOVER"], _P["MENU_ACTIVE"]
PENDING_BG, CODE_BG = _P["PENDING_BG"], _P["CODE_BG"]
FONT = ("Segoe UI", 10)
DOT_SIZE = 18                      # size of the dot when minimized (bigger number = bigger dot)
SMALL = ("Segoe UI", 9)
BOLD = ("Segoe UI Semibold", 10)
TONES = {"good": GOOD, "warn": WARN, "err": BAD, "": MUTED}


class Btn(tk.Frame):
    """A flat button with a 1px border and the hotkey shown on the right."""
    STYLES = {   # kind: (background, text, key hint, border, hover)
        "pri": _P["PRI"],
        "sec": (BG, FG, MUTED, BORDER, HOVER),
        "stop": (BAD, "#ffffff", _P["STOP_KEY"], BAD, _P["STOP_HOVER"]),
        "on": (_P["ON_BG"], GOOD, GOOD, GOOD, _P["ON_HOVER"]),
    }

    def __init__(self, parent, text, key, cmd, kind="sec"):
        super().__init__(parent, padx=1, pady=1, cursor="hand2")
        self.cmd, self.kind, self.hover = cmd, kind, False
        self.inner = tk.Frame(self)
        self.inner.pack(fill="both", expand=True)
        self.lbl = tk.Label(self.inner, text=text, font=BOLD, pady=7, anchor="center")
        self.lbl.pack(side="left", fill="both", expand=True, padx=(10, 2))
        self.key = tk.Label(self.inner, text=key, font=SMALL)
        if key:
            self.key.pack(side="right", padx=(0, 9))
        for w in (self, self.inner, self.lbl, self.key):
            w.bind("<Button-1>", lambda e: self.cmd())
            w.bind("<Enter>", lambda e: self._hover(True))
            w.bind("<Leave>", lambda e: self._hover(False))
        self.paint()

    def _hover(self, on):
        self.hover = on
        self.paint()

    def paint(self):
        kind = "sec" if ALL_WHITE_BUTTONS else self.kind   # every button plain white
        bg, fg, kfg, border, hov = self.STYLES[kind]
        bg = hov if self.hover else bg
        self.configure(bg=border)
        for w in (self.inner, self.lbl, self.key):
            w.configure(bg=bg)
        self.lbl.configure(fg=fg)
        self.key.configure(fg=kfg)

    def set(self, text=None, kind=None, key=None):
        if text is not None:
            self.lbl.configure(text=text)
        if key is not None:
            self.key.configure(text=key)
        if kind is not None:
            self.kind = kind
        self.paint()


class Check(tk.Checkbutton):
    def __init__(self, parent, text, var, cmd):
        super().__init__(parent, text=text, variable=var, command=cmd, font=FONT,
                         justify="left", anchor="w",
                         bg=BG, fg=FG, selectcolor=BG, activebackground=BG,
                         activeforeground=FG, highlightthickness=0, bd=0, cursor="hand2")


class ChatWindow:
    def __init__(self):
        ui_cfg = cfg.setdefault("ui", {})
        self.root = root = tk.Tk()
        root.title("Laptop AI")
        self.f = f = max(1.0, root.winfo_fpixels("1i") / 96.0)   # screen scale (125%, 150%...)
        root.geometry(ui_cfg.get("geometry", f"{int(520 * f)}x{int(700 * f)}"))
        root.minsize(int(260 * f), int(180 * f))
        root.configure(bg=BG)
        root.protocol("WM_DELETE_WINDOW", self.quit)
        self.mini = False
        self.full_geometry = None
        self.cols = 0

        style = ttk.Style(root)
        style.theme_use("clam")
        style.configure("Dark.Vertical.TScrollbar", background=_P["SCROLL"], troughcolor=BG,
                        bordercolor=BG, arrowcolor=MUTED, lightcolor=_P["SCROLL"], darkcolor=_P["SCROLL"])
        style.map("Dark.Vertical.TScrollbar", background=[("active", _P["SCROLL_ACTIVE"])])

        # ---------- header: title, chat size, status ----------
        head = tk.Frame(root, bg=BG, padx=14, pady=4)
        head.pack(fill="x", side="top")
        self.info = tk.Label(head, text="", font=SMALL, bg=BG, fg=MUTED)
        self.info.pack(side="left")
        self.status = tk.Label(root, text="", font=SMALL, bg=BG, fg=MUTED, anchor="w",
                               justify="left", padx=14)

        # ---------- toolbar: AI picker, settings, mini ----------
        self.top = tk.Frame(root, bg=BG, padx=12)
        self.top.pack(fill="x", side="top")
        self.top.columnconfigure(0, weight=1)
        self.bar = bar = tk.Frame(self.top, bg=BG)          # AI picker + model
        bar.grid(row=0, column=0, sticky="w")
        self.right = tk.Frame(self.top, bg=BG)              # Settings + Mini
        self.right.grid(row=0, column=1, sticky="e")
        self.toolbar_wrapped = False
        self.status.pack(fill="x", side="top", pady=(6, 0))
        self.prov = tk.StringVar(value=provider_name())
        holder = tk.Frame(bar, bg=BORDER, padx=1, pady=1)
        holder.pack(side="left")
        menu = tk.OptionMenu(holder, self.prov, *[PROVIDER_NAMES[p] for p in PROVIDERS],
                             command=self.pick_provider)
        menu.configure(bg=BG, fg=FG, activebackground=HOVER, activeforeground=FG, font=BOLD,
                       highlightthickness=0, bd=0, relief="flat", indicatoron=True, padx=10, pady=4,
                       cursor="hand2")
        menu["menu"].configure(bg=BG, fg=FG, activebackground=MENU_ACTIVE, activeforeground=FG,
                               font=FONT, bd=0)
        menu.pack()
        self.model = tk.StringVar(value=MODEL_NAMES[cfg["model"]])
        self.model_holder = tk.Frame(bar, bg=BORDER, padx=1, pady=1)
        mmenu = tk.OptionMenu(self.model_holder, self.model, *MODEL_NAMES.values(),
                              command=self.pick_model)
        mmenu.configure(bg=BG, fg=FG, activebackground=HOVER, activeforeground=FG, font=BOLD,
                        highlightthickness=0, bd=0, relief="flat", padx=10, pady=4, cursor="hand2")
        mmenu["menu"].configure(bg=BG, fg=FG, activebackground=MENU_ACTIVE, activeforeground=FG,
                                font=FONT, bd=0)
        mmenu.pack()
        self.f7 = tk.Label(bar, text="F7", font=SMALL, bg=BG, fg=DIM)
        self.f7.pack(side="left", padx=(6, 0))
        self.show_model_picker(cfg["provider"])
        self.mini_btn = Btn(self.right, "Mini", "", self.toggle_mini)
        self.mini_btn.pack(side="right")
        self.set_btn = Btn(self.right, "Settings", "", self.toggle_settings)
        self.set_btn.pack(side="right", padx=(0, 6))

        # ---------- settings (hidden until opened) ----------
        self.settings = tk.Frame(root, bg=BG, padx=14, pady=6)
        box = tk.Frame(self.settings, bg=BORDER, padx=1, pady=1)
        box.pack(fill="x")
        inner = tk.Frame(box, bg=BG, padx=10, pady=8)
        inner.pack(fill="x")
        self.typeit = tk.BooleanVar(value=ui_cfg.get("type_answers", state["type_answers"]))
        self.ontop = tk.BooleanVar(value=ui_cfg.get("ontop", True))
        self.paste = tk.BooleanVar(value=ui_cfg.get("output_mode", state["output_mode"]) == "paste")
        self.codeed = tk.BooleanVar(value=ui_cfg.get("code_editor", state["code_editor"]))
        self.checks = [
            Check(inner, "Type the answer automatically after G", self.typeit, self.apply_settings),
            Check(inner, "Paste instead of typing (instant, keeps indentation)", self.paste, self.apply_settings),
            Check(inner, "Typing into a code editor (stops autocomplete, keeps indentation)",
                  self.codeed, self.apply_settings),
            Check(inner, "Keep this window on top", self.ontop, self.apply_settings),
        ]
        for ch in self.checks:
            ch.pack(anchor="w", fill="x")

        # typing speed, same as the phone page: delay per key 1-60 ms
        self.delay = tk.IntVar(value=int(ui_cfg.get("type_delay_ms", state["type_delay_ms"])))
        self.delay_lbl = tk.Label(inner, text="", font=SMALL, bg=BG, fg=MUTED, anchor="w")
        self.delay_lbl.pack(fill="x", pady=(10, 0))
        tk.Scale(inner, from_=1, to=60, orient="horizontal", variable=self.delay, showvalue=0,
                 command=lambda v: self.apply_settings(), bg=FG, fg=FG, troughcolor=_P["TROUGH"],
                 activebackground=FG, highlightthickness=0, bd=0, sliderrelief="flat",
                 width=int(10 * self.f), sliderlength=int(22 * self.f), cursor="hand2"
                 ).pack(fill="x", pady=(2, 0))
        Btn(inner, "Edit answer rules", "", self.edit_rules).pack(anchor="w", pady=(8, 0))
        self.settings_open = False

        # ---------- bottom part (packed bottom-up so the chat shrinks first) ----------
        self.grid_frame = tk.Frame(root, bg=BG, padx=11, pady=8)
        self.grid_frame.pack(fill="x", side="bottom")
        act = self.act
        self.btns = [
            Btn(self.grid_frame, "Add", "X", act(lambda: jobs.put(("btn", "send")))),
            Btn(self.grid_frame, "Ask AI", "G", act(lambda: jobs.put(("btn", "ask"))), "pri"),
            Btn(self.grid_frame, "Screen read", "O", act(lambda: jobs.put(("btn", "ocr")))),
            Btn(self.grid_frame, "Type", "T", self.type_or_pause),
            Btn(self.grid_frame, "New chat", "M", act(lambda: jobs.put(("newchat",)))),
            Btn(self.grid_frame, "Pause", "F8", toggle_pause),
            Btn(self.grid_frame, "STOP", "F9", do_stop),   # plain white, like the other buttons
        ]
        self.type_btn, self.pause_btn = self.btns[3], self.btns[5]

        self.input_frame = tk.Frame(root, bg=BG, padx=14, pady=4)
        self.input_frame.pack(fill="x", side="bottom")
        self.entry_border = tk.Frame(self.input_frame, bg=BORDER, padx=1, pady=1)
        self.entry_border.pack(side="left", fill="both", expand=True)
        self.entry = tk.Text(self.entry_border, height=3, width=10, wrap="word", bg=BG, fg=FG,
                             insertbackground=FG, font=FONT, bd=0, padx=10, pady=8,
                             highlightthickness=0)
        self.entry.pack(fill="both", expand=True)
        self.entry.bind("<Return>", self.on_enter)
        self.entry.bind("<FocusIn>", lambda e: self.entry_border.configure(bg=FG))
        self.entry.bind("<FocusOut>", lambda e: self.entry_border.configure(bg=BORDER))
        self.send_btn = Btn(self.input_frame, "Send", "", self.send, "pri")
        self.send_btn.pack(side="right", fill="y", padx=(8, 0))

        self.pending = tk.Frame(root, bg=BG, padx=14, pady=4)
        pbox = tk.Frame(self.pending, bg=YOU_C, padx=1, pady=1)
        pbox.pack(fill="x")
        pin = tk.Frame(pbox, bg=PENDING_BG, padx=10, pady=6)
        pin.pack(fill="x")
        top = tk.Frame(pin, bg=PENDING_BG)
        top.pack(fill="x")
        tk.Label(top, text="SELECTED  -  goes with your next question", font=("Segoe UI Semibold", 8),
                 bg=PENDING_BG, fg=YOU_C).pack(side="left")
        clear = tk.Label(top, text="clear", font=SMALL, bg=PENDING_BG, fg=MUTED, cursor="hand2")
        clear.pack(side="right")
        clear.bind("<Button-1>", lambda e: set_buffer(""))
        self.pending_text = tk.Label(pin, text="", font=SMALL, bg=PENDING_BG, fg=FG,
                                     anchor="w", justify="left")
        self.pending_text.pack(fill="x")

        # ---------- the chat (fills what is left) ----------
        self.chat_frame = tk.Frame(root, bg=BG, padx=14, pady=4)
        self.chat_frame.pack(fill="both", expand=True, side="top")
        cbox = tk.Frame(self.chat_frame, bg=BORDER, padx=1, pady=1)
        cbox.pack(fill="both", expand=True)
        self.chat = tk.Text(cbox, wrap="word", bg=BG, fg=FG, font=FONT, bd=0, padx=12, pady=10,
                            highlightthickness=0, state="disabled", cursor="arrow")
        sb = ttk.Scrollbar(cbox, orient="vertical", command=self.chat.yview,
                           style="Dark.Vertical.TScrollbar")
        self.chat.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.chat.pack(side="left", fill="both", expand=True)
        c = self.chat
        c.tag_configure("who_user", foreground=YOU_C, font=("Segoe UI Semibold", 9), spacing1=12)
        c.tag_configure("who_ai", foreground=AI_C, font=("Segoe UI Semibold", 9), spacing1=12)
        c.tag_configure("who_err", foreground=BAD, font=("Segoe UI Semibold", 9), spacing1=12)
        c.tag_configure("body", lmargin1=2, lmargin2=2, spacing3=2)
        c.tag_configure("code", font=("Consolas", 10), lmargin1=2, lmargin2=2, spacing3=2,
                        background=CODE_BG)
        c.tag_configure("note", foreground=DIM, justify="center", spacing1=8, spacing3=8, font=SMALL)
        c.bind("<Control-c>", lambda e: None)    # normal copy from the chat works

        self.apply_settings(save=False)
        self.show_history()
        root.bind("<Configure>", self.on_resize)
        root.bind("<Unmap>", self.on_unmap)
        self.dot = None
        self.busy = self.typing = False
        self.last_tone = ""
        root.after(100, self.poll)
        root.update_idletasks()
        state["ui_hwnd"] = int(root.wm_frame(), 16)
        self.relayout()
        if ui_cfg.get("mini"):
            self.toggle_mini()
        self.entry.focus_set()

    # ---------- layout ----------
    def on_resize(self, event):
        if event.widget is self.root:
            self.relayout()

    def relayout(self):
        w = self.root.winfo_width() - int(22 * self.f)
        need = max(b.winfo_reqwidth() for b in self.btns) + int(6 * self.f)
        cols = max(2, min(len(self.btns), w // need))
        if cols in (5, 6):                       # 7 buttons: 4 + 3 looks better than 6 + 1
            cols = 4
        if cols != self.cols:
            self.cols = cols
            for i in range(len(self.btns)):
                self.grid_frame.columnconfigure(i, weight=1 if i < cols else 0, uniform="b" if i < cols else "")
            for i, b in enumerate(self.btns):
                last = i == len(self.btns) - 1   # STOP fills the rest of its row
                span = cols - (i % cols) if last else 1
                b.grid(row=i // cols, column=i % cols, columnspan=span, sticky="nsew", padx=3, pady=3)
        h, width = self.root.winfo_height(), self.root.winfo_width()
        # toolbar: Settings + Mini move to a 2nd line when they do not fit next to the AI pickers
        need = self.bar.winfo_reqwidth() + self.right.winfo_reqwidth() + int(34 * self.f)
        wrap = width < need
        if wrap != self.toolbar_wrapped:
            self.toolbar_wrapped = wrap
            if wrap:
                self.right.grid(row=1, column=0, columnspan=2, sticky="w", pady=(6, 0))
            else:
                self.right.grid(row=0, column=1, columnspan=1, sticky="e", pady=0)
        for ch in self.checks:
            ch.configure(wraplength=max(120, width - int(80 * self.f)))
        short = h < 600 * self.f
        self.entry.configure(height=1 if short else 3)
        self.pending_text.configure(wraplength=max(150, width - int(60 * self.f)))
        self.status.configure(wraplength=max(150, width - int(30 * self.f)))
        narrow = width < 400 * self.f
        if narrow and self.f7.winfo_ismapped():
            self.f7.pack_forget()
            self.info.pack_forget()
        elif not narrow and not self.f7.winfo_ismapped():
            self.f7.pack(side="left", padx=(6, 0), after=self.bar.winfo_children()[0])
            self.show_model_picker(cfg["provider"])
            self.info.pack(side="left")

    def toggle_mini(self):
        self.mini = not self.mini
        if self.mini:
            self.full_geometry = self.root.geometry()
            self.chat_frame.pack_forget()
            self.input_frame.pack_forget()
            self.settings.pack_forget()
            self.settings_open = False
            self.set_btn.pack_forget()
            self.mini_btn.set("Full")
            self.root.after(30, self.fit_mini)
        else:
            self.input_frame.pack(fill="x", side="bottom", before=self.grid_frame)
            if self.pending.winfo_ismapped():
                self.pending.pack(fill="x", side="bottom", before=self.input_frame)
            self.chat_frame.pack(fill="both", expand=True, side="top")
            self.set_btn.pack(side="right", padx=(0, 6), before=self.mini_btn)
            self.mini_btn.set("Mini")
            if self.full_geometry:
                self.root.geometry(self.full_geometry)
        cfg["ui"]["mini"] = self.mini
        save_settings()

    def fit_mini(self):
        if self.mini:
            self.root.update_idletasks()
            self.root.geometry(f"{self.root.winfo_width()}x{self.root.winfo_reqheight()}")

    def type_or_pause(self):
        """Type button: types the answer, or pauses / resumes while typing."""
        if state["typing"]:
            toggle_typing_pause(from_button=True)
        else:
            self.act(lambda: jobs.put(("btn", "type")))()

    def act(self, fn):
        """A button that does something: close Settings first so the chat shows again."""
        def run():
            if self.settings_open:
                self.toggle_settings()
            fn()
        return run

    def toggle_settings(self):
        self.settings_open = not self.settings_open
        if self.settings_open:
            # packed late so the buttons keep their space; it still shows under the status line
            self.settings.pack(fill="x", side="top", before=self.chat_frame)
            self.set_btn.set(kind="on")
        else:
            self.settings.pack_forget()
            self.set_btn.set(kind="sec")

    def apply_settings(self, save=True):
        ms = int(self.delay.get())
        state["type_delay_ms"] = ms
        state["code_editor"] = self.codeed.get()
        note = "  (not used while Paste is on)" if self.paste.get() else ""
        self.delay_lbl.configure(text=f"Typing speed: {ms} ms per key  (lower = faster){note}")
        state["type_answers"] = self.typeit.get()
        state["output_mode"] = "paste" if self.paste.get() else "type"
        self.root.attributes("-topmost", self.ontop.get())
        if save:
            cfg["ui"].update(type_answers=state["type_answers"], output_mode=state["output_mode"],
                             type_delay_ms=state["type_delay_ms"], code_editor=state["code_editor"],
                             ontop=self.ontop.get())
            save_settings()

    def edit_rules(self):
        try:
            os.startfile(RULES_FILE)
        except Exception as e:
            log(f"Could not open answer_rules.txt: {e}", "err")

    # ---------- minimized = a small floating dot ----------
    def on_unmap(self, event):
        if event.widget is self.root and self.dot is None:
            self.root.after(10, self._maybe_dot)

    def _maybe_dot(self):
        if self.dot is None and self.root.state() == "iconic":
            self.show_dot()

    def show_dot(self):
        f = self.f
        size = int(DOT_SIZE * f)
        self.root.withdraw()                     # no taskbar button while it is a dot
        d = self.dot = tk.Toplevel(self.root)
        d.overrideredirect(True)                 # no title bar, no buttons
        d.attributes("-topmost", True)
        clear = "#010203"                        # this colour becomes see-through
        d.configure(bg=clear)
        d.attributes("-transparentcolor", clear)
        c = self.dot_canvas = tk.Canvas(d, width=size, height=size, bg=clear,
                                        highlightthickness=0, cursor="hand2")
        c.pack()
        # a plain white dot; the thin grey edge keeps it visible on white pages
        self.dot_ring = c.create_oval(1, 1, size - 2, size - 2, fill="#ffffff", outline="#888888", width=1)

        pos = cfg.get("ui", {}).get("dot_pos")
        if pos:
            x, y = pos
        else:                                    # right edge, middle of the screen
            x = d.winfo_screenwidth() - size - int(16 * f)
            y = d.winfo_screenheight() // 2
        d.geometry(f"{size}x{size}+{int(x)}+{int(y)}")

        c.bind("<ButtonPress-1>", self._dot_press)
        c.bind("<B1-Motion>", self._dot_drag)
        c.bind("<ButtonRelease-1>", self._dot_release)
        c.bind("<Button-3>", self._dot_menu)
        d.update_idletasks()
        state["dot_hwnd"] = user32.GetAncestor(d.winfo_id(), 2)
        self.update_dot()

    def _dot_press(self, e):
        self._drag = (e.x_root, e.y_root, self.dot.winfo_x(), self.dot.winfo_y(), False)

    def _dot_drag(self, e):
        x0, y0, wx, wy, _ = self._drag
        dx, dy = e.x_root - x0, e.y_root - y0
        if abs(dx) + abs(dy) > 4:
            self._drag = (x0, y0, wx, wy, True)
        self.dot.geometry(f"+{wx + dx}+{wy + dy}")

    def _dot_release(self, e):
        if self._drag[4]:                        # it was a drag: remember the spot
            cfg.setdefault("ui", {})["dot_pos"] = [self.dot.winfo_x(), self.dot.winfo_y()]
            save_settings()
        else:
            self.restore_from_dot()              # a click: open the window again

    def _dot_menu(self, e):
        m = tk.Menu(self.dot, tearoff=0, bg=BG, fg=FG, activebackground=MENU_ACTIVE,
                    activeforeground=FG, font=FONT, bd=0)
        m.add_command(label="Open", command=self.restore_from_dot)
        m.add_command(label="Stop (F9)", command=do_stop)
        m.add_separator()
        m.add_command(label="Quit", command=self.quit)
        m.tk_popup(e.x_root, e.y_root)

    def toggle_window(self):
        """` ` : open the window if it is a dot / minimized, otherwise minimize it."""
        if self.dot is not None or self.root.state() in ("iconic", "withdrawn"):
            self.restore_from_dot()
            self.root.attributes("-topmost", True)            # make sure it comes to the front
            self.root.after(300, lambda: self.root.attributes("-topmost", self.ontop.get()))
        else:
            self.root.iconify()                               # becomes the dot

    def restore_from_dot(self):
        if self.dot is not None:
            self.dot.destroy()
            self.dot = None
            state["dot_hwnd"] = None
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()

    def update_dot(self):
        """The dot stays plain white."""
        return

    # ---------- input ----------
    def on_enter(self, event):
        if event.state & 0x1:                    # Shift+Enter = new line
            return None
        self.send()
        return "break"

    def send(self):
        text = self.entry.get("1.0", "end").strip()
        if not text and not state["buffer"]:
            return
        self.entry.delete("1.0", "end")
        jobs.put(("chat", text))

    def pick_model(self, value):
        m = next((k for k, v in MODEL_NAMES.items() if v == value), "opus")
        if m != cfg["model"]:
            jobs.put(("model", m))

    def show_model_picker(self, prov):
        """Opus / Sonnet only matters for Claude Code and Claude, not Gemini."""
        if prov in ("claude", "local"):
            self.model_holder.pack(side="left", padx=(6, 0), after=self.bar.winfo_children()[0])
        else:
            self.model_holder.pack_forget()

    def pick_provider(self, value):
        p = next((k for k, v in PROVIDER_NAMES.items() if v == value), "gemini")
        if p != cfg["provider"]:
            jobs.put(("switch", p))

    def quit(self):
        try:
            if not self.mini:
                cfg["ui"]["geometry"] = self.root.geometry()
            save_settings()
            listener.stop()
        finally:
            self.root.destroy()
            os._exit(0)

    # ---------- the chat ----------
    def add_msg(self, role, text, name=None):
        c = self.chat
        c.configure(state="normal")
        if role == "user":
            c.insert("end", "YOU\n", "who_user")
        elif role == "assistant":
            c.insert("end", (name or "AI").upper() + "\n", "who_ai")
        else:
            c.insert("end", "PROBLEM\n", "who_err")
        looks_like_code = role == "assistant" and re.search(r"[{};]\s*$|^\s{2,}\S|^//|^#\w", text, re.M)
        c.insert("end", text.strip() + "\n", "code" if looks_like_code else "body")
        c.configure(state="disabled")
        c.see("end")
        self.update_info()

    def note(self, text):
        c = self.chat
        c.configure(state="normal")
        c.insert("end", text + "\n", "note")
        c.configure(state="disabled")
        c.see("end")

    def update_info(self):
        n = sum(1 for t in state["history"] if t["role"] == "user")
        self.info.configure(text=f"{n} question{'s' if n != 1 else ''} in this chat" if n else "new chat")

    def show_history(self):
        h = state["history"]
        if h:
            self.note(f"earlier in this chat  -  New chat (M) forgets it")
            for t in h:
                name = answer_label(t.get("prov"), t.get("model")) if t["role"] == "assistant" else None
                self.add_msg(t["role"], t["text"], name)
        else:
            self.note("Select text anywhere and press G, or type below.")
        self.update_info()

    # ---------- messages from the background thread ----------
    def poll(self):
        try:
            while True:
                item = ui_q.get_nowait()
                kind = item[0]
                if kind == "msg":
                    self.add_msg(item[1], item[2], item[3])
                elif kind == "status":
                    self.status.configure(text=item[1], fg=TONES.get(item[2] if len(item) > 2 else "", MUTED))
                    self.last_tone = item[2] if len(item) > 2 else ""
                    if self.mini:
                        self.root.after(30, self.fit_mini)
                elif kind == "pending":
                    if item[1]:
                        self.pending_text.configure(text=preview(item[1], 220))
                        anchor = self.grid_frame if self.mini else self.input_frame
                        self.pending.pack(fill="x", side="bottom", before=anchor)
                    else:
                        self.pending.pack_forget()
                    if self.mini:
                        self.root.after(30, self.fit_mini)
                elif kind == "busy":
                    self.busy = item[1]
                    self.send_btn.set("..." if item[1] else "Send", "sec" if item[1] else "pri")
                elif kind == "ready":
                    self.type_btn.set("Type answer" if item[1] else "Type", "on" if item[1] else "sec")
                elif kind == "typing":
                    self.typing = item[1]
                    if item[1]:
                        self.type_btn.set("Pause typing", "on", "Space")
                    else:
                        self.type_btn.set("Type", "sec", "T")
                elif kind == "typing_paused":
                    if self.typing:
                        if item[1]:
                            self.type_btn.set("Resume typing", "pri", "Space")
                        else:
                            self.type_btn.set("Pause typing", "on", "Space")
                elif kind == "paused":
                    self.pause_btn.set("Resume" if item[1] else "Pause", "on" if item[1] else "sec")
                elif kind == "clear":
                    c = self.chat
                    c.configure(state="normal")
                    c.delete("1.0", "end")
                    c.configure(state="disabled")
                    self.note("Select text anywhere and press G, or type below.")
                    self.update_info()
                elif kind == "provider":
                    self.prov.set(provider_name(item[1]))
                    self.show_model_picker(item[1])
                elif kind == "toggle_window":
                    self.toggle_window()
                elif kind == "model":
                    self.model.set(MODEL_NAMES[item[1]])
        except queue.Empty:
            pass
        self.update_dot()
        self.root.after(100, self.poll)


def ensure_key():
    """Only asks if the AI in use has no key at all (never for the other one)."""
    if key_for():
        return
    try:
        k = input(f"Paste your {provider_name()} API key: ").strip()
    except EOFError:
        k = ""
    if k:
        cfg["claude_key" if cfg["provider"] == "claude" else "gemini_key"] = k
        save_settings()


if __name__ == "__main__":
    ensure_key()
    print("LAPTOP AI running. Chat window is open.")
    print("Select text, then: X = add, G = ask, M = new chat, O = screen read. F9 stop, F7 switch AI, F8 pause.")
    win = ChatWindow()
    threading.Thread(target=worker, daemon=True).start()
    mouse.Listener(on_click=on_click, on_scroll=on_scroll).start()
    listener.start()
    log(f"Ready. AI: {provider_name()}. Select text + G, or type below.")
    try:
        win.root.mainloop()
    except KeyboardInterrupt:
        pass
    os._exit(0)
