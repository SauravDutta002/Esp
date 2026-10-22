# """
# Select text with the mouse, then press X to send it to the ESP32 (phone).

# Method 1: read the selection directly from the app (UI Automation). No keys, no clipboard.
# Method 2: if the app doesn't expose it, the script copies the selection itself
#           (only right after a mouse selection) and restores your old clipboard.

# X is swallowed only when a selection is being sent. Otherwise X types normally.
# Ctrl/Alt/Win + X are never touched.
# F8 = pause / resume.   Stop the script with Ctrl+C in this terminal.
# """
# import ctypes
# import math
# import queue
# import threading
# import time

# import pyperclip
# import requests
# import uiautomation as auto
# from pynput import keyboard, mouse

# ESP = "http://10.254.65.150"       # the ESP32's fixed address
# SEND_VK = 0x58                     # X key. Other keys: F9 = 0x78, Z = 0x5A
# DRAG_PX = 8                        # movement that counts as a drag-select
# MULTI_CLICK_S = 0.45               # double / triple-click window
# ARMED_FOR_S = 30                   # fallback copy is allowed this long after a selection
# MAX_CHARS = 20000
# TERMINALS = ("ConsoleWindowClass", "CASCADIA_HOSTING_WINDOW_CLASS")

# user32 = ctypes.windll.user32
# kb = keyboard.Controller()
# jobs = queue.Queue()
# state = {
#     "enabled": True,
#     "press": (0, 0),
#     "last_up": 0.0,
#     "armed_at": 0.0,               # time of the last mouse selection, 0 = none
#     "last_key": 0.0,
# }
# MODIFIER_VKS = {0x10, 0x11, 0x12, 0xA0, 0xA1, 0xA2, 0xA3, 0xA4, 0xA5, 0x5B, 0x5C}


# def held(vk):
#     return (user32.GetAsyncKeyState(vk) & 0x8000) != 0


# def foreground_class():
#     buf = ctypes.create_unicode_buffer(256)
#     user32.GetClassNameW(user32.GetForegroundWindow(), buf, 256)
#     return buf.value


# def armed():
#     return state["armed_at"] and (time.time() - state["armed_at"]) < ARMED_FOR_S


# # ---------- Method 1: direct read ----------
# def read_direct():
#     try:
#         with auto.UIAutomationInitializerInThread():
#             ctrl = auto.GetFocusedControl()
#             for _ in range(4):                   # browsers expose text on a parent
#                 if ctrl is None:
#                     break
#                 try:
#                     pattern = ctrl.GetTextPattern()
#                 except Exception:
#                     pattern = None
#                 if pattern:
#                     text = "".join(r.GetText(MAX_CHARS) for r in pattern.GetSelection())
#                     if text:
#                         return text
#                 ctrl = ctrl.GetParentControl()
#     except Exception:
#         pass
#     return None


# # ---------- Method 2: script copies the selection ----------
# def read_by_copy():
#     if foreground_class() in TERMINALS:          # Ctrl+C would interrupt a running program
#         return None
#     try:
#         old = pyperclip.paste()
#     except Exception:
#         old = None
#     try:
#         pyperclip.copy("")
#     except Exception:
#         return None

#     time.sleep(0.05)
#     with kb.pressed(keyboard.Key.ctrl):
#         kb.press("c")
#         kb.release("c")

#     text = ""
#     for _ in range(12):                          # wait up to ~0.6 s for the copy
#         time.sleep(0.05)
#         try:
#             text = pyperclip.paste()
#         except Exception:
#             text = ""
#         if text:
#             break

#     if old is not None:
#         try:
#             pyperclip.copy(old)                  # put the previous clipboard back
#         except Exception:
#             pass
#     return text or None


# def send(text, how):
#     try:
#         r = requests.post(ESP + "/p", data={"t": text[:MAX_CHARS]}, timeout=5)
#         print(f"Sent {len(text)} chars ({how}) -> {r.status_code}")
#     except Exception as e:
#         print("Send failed:", e)


# def worker():
#     while True:
#         jobs.get()
#         while not jobs.empty():                  # collapse repeats into one job
#             jobs.get_nowait()
#         time.sleep(0.05)
#         text = read_direct()
#         how = "direct read"
#         if not text and armed():
#             text = read_by_copy()
#             how = "copied by script"
#         if text and text.strip():
#             send(text, how)
#         else:
#             print("Nothing to send (no selection found).")


# # ---------- mouse: arm the fallback after a selection ----------
# def on_click(x, y, button, pressed):
#     if button != mouse.Button.left:
#         return
#     now = time.time()
#     if pressed:
#         state["press"] = (x, y)
#         return
#     dragged = math.hypot(x - state["press"][0], y - state["press"][1]) > DRAG_PX
#     multi = (now - state["last_up"]) < MULTI_CLICK_S
#     state["last_up"] = now
#     state["armed_at"] = now if (dragged or multi) else 0.0


# # ---------- keyboard: runs before Windows delivers each key ----------
# def key_filter(msg, data):
#     if msg not in (0x100, 0x104):                # key down / sys key down
#         return
#     if data.flags & 0x10:                        # ignore keys sent by this script
#         return

#     vk = data.vkCode
#     if vk == 0x77:                               # F8 toggles pause
#         state["enabled"] = not state["enabled"]
#         print("Send key:", "ON" if state["enabled"] else "PAUSED")
#         return

#     if vk != SEND_VK:
#         if vk not in MODIFIER_VKS:
#             state["armed_at"] = 0.0              # you typed something: disarm the fallback
#         return
#     if not state["enabled"]:
#         return
#     if held(0x11) or held(0x12) or held(0x5B) or held(0x5C):   # Ctrl / Alt / Win
#         return

#     now = time.time()
#     if armed():
#         # You just selected with the mouse: X means "send". Swallow it.
#         if now - state["last_key"] > 0.3:        # ignore key auto-repeat
#             state["last_key"] = now
#             jobs.put(1)
#         listener.suppress_event()
#         return

#     # Not armed (for example a keyboard selection): send only if the app exposes it
#     text = read_direct()
#     if text and text.strip():
#         if now - state["last_key"] > 0.3:
#             state["last_key"] = now
#             send(text, "direct read")
#         listener.suppress_event()
#     # otherwise X types normally


# listener = keyboard.Listener(win32_event_filter=key_filter)

# if __name__ == "__main__":
#     print(f"Running. Select text with the mouse, then press X. F8 pauses. Target: {ESP}")
#     threading.Thread(target=worker, daemon=True).start()
#     mouse.Listener(on_click=on_click).start()
#     listener.start()
#     try:
#         listener.join()
#     except KeyboardInterrupt:
#         print("Stopped.")





"""
Select text with the mouse, then use these keys (right after selecting):

  X   add the selection to the text on the phone (always adds, never replaces)
  M   delete everything (clears the phone and the stored text)
  F8  pause / resume the whole script

The keys are swallowed only right after a mouse selection. With nothing
selected they type normally. Shift/Ctrl/Alt/Win + key is never touched.

Method 1: read the selection directly from the app (UI Automation).
Method 2: if the app doesn't expose it, the script copies it itself and
          restores your old clipboard.
Stop the script with Ctrl+C in this terminal.
"""
import ctypes
import math
import queue
import threading
import time

import pyperclip
import requests
import uiautomation as auto
from pynput import keyboard, mouse

# Tried in order. Add the IP the ESP32 prints in the Serial Monitor.
ESP_LIST = ["http://10.254.65.150", "http://paste.local"]
ESP = ESP_LIST[0]

VK_SEND, VK_CLEAR = 0x58, 0x4D     # X, M
DRAG_PX = 8                        # movement that counts as a drag-select
MULTI_CLICK_S = 0.45               # double / triple-click window
ARMED_FOR_S = 30                   # keys work this long after a selection
MAX_CHARS = 20000
SEPARATOR = "\n"                   # placed between added selections
TERMINALS = ("ConsoleWindowClass", "CASCADIA_HOSTING_WINDOW_CLASS")

user32 = ctypes.windll.user32
kb = keyboard.Controller()
jobs = queue.Queue()
state = {
    "enabled": True,
    "buffer": "",                  # everything sent so far
    "last_piece": "",
    "press": (0, 0),
    "last_up": 0.0,
    "armed_at": 0.0,
    "last_key": 0.0,
}
MODIFIER_VKS = {0x10, 0x11, 0x12, 0xA0, 0xA1, 0xA2, 0xA3, 0xA4, 0xA5, 0x5B, 0x5C}


def held(vk):
    return (user32.GetAsyncKeyState(vk) & 0x8000) != 0


def any_modifier():
    return held(0x10) or held(0x11) or held(0x12) or held(0x5B) or held(0x5C)


def foreground_class():
    buf = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(user32.GetForegroundWindow(), buf, 256)
    return buf.value


def armed():
    return state["armed_at"] and (time.time() - state["armed_at"]) < ARMED_FOR_S


# ---------- talking to the ESP32 ----------
def post_esp(data):
    """Try the last working address first, then the others."""
    global ESP
    order = [ESP] + [a for a in ESP_LIST if a != ESP]
    for addr in order:
        try:
            r = requests.post(addr + "/p", data=data, timeout=2)
            ESP = addr
            return r.status_code
        except Exception:
            continue
    return None


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


# ---------- actions ----------
def do_send():
    text = read_direct()
    how = "direct read"
    if not text and armed():
        text = read_by_copy()
        how = "copied by script"
    if not text or not text.strip():
        print("Nothing to send (no selection found).")
        return

    if text == state["last_piece"]:
        print("Same text as the last one, not added twice.")
        return

    if state["buffer"]:
        state["buffer"] = (state["buffer"] + SEPARATOR + text)[:MAX_CHARS]
    else:
        state["buffer"] = text[:MAX_CHARS]
    state["last_piece"] = text

    code = post_esp({"t": state["buffer"]})
    if code is None:
        print("Send failed: ESP32 not reachable. Is the laptop on the 'IOT' hotspot?")
    else:
        print(f"ADDED {len(text)} chars ({how}); phone now has {len(state['buffer'])} chars -> {code}")


def do_clear():
    state["buffer"] = ""
    state["last_piece"] = ""
    code = post_esp({"clear": "1"})
    print("CLEARED everything." if code else "Clear failed: ESP32 not reachable.")


def worker():
    while True:
        job = jobs.get()
        while not jobs.empty():                  # collapse repeats into one job
            job = jobs.get_nowait()
        time.sleep(0.05)
        if job == "send":
            do_send()
        elif job == "clear":
            do_clear()


# ---------- mouse: arm the keys after a selection ----------
def on_click(x, y, button, pressed):
    if button != mouse.Button.left:
        return
    now = time.time()
    if pressed:
        state["press"] = (x, y)
        return
    dragged = math.hypot(x - state["press"][0], y - state["press"][1]) > DRAG_PX
    multi = (now - state["last_up"]) < MULTI_CLICK_S
    state["last_up"] = now
    state["armed_at"] = now if (dragged or multi) else 0.0


# ---------- keyboard: runs before Windows delivers each key ----------
def key_filter(msg, data):
    if msg not in (0x100, 0x104):                # key down / sys key down
        return
    if data.flags & 0x10:                        # ignore keys sent by this script
        return

    vk = data.vkCode
    now = time.time()

    if vk == 0x77:                               # F8 toggles pause
        state["enabled"] = not state["enabled"]
        print("Keys:", "ON" if state["enabled"] else "PAUSED")
        return

    if vk in (VK_SEND, VK_CLEAR) and state["enabled"] and not any_modifier():
        if vk == VK_SEND:
            if armed():
                if now - state["last_key"] > 0.3:    # ignore key auto-repeat
                    state["last_key"] = now
                    jobs.put("send")
                listener.suppress_event()
            else:
                # no mouse selection: send only if the app exposes a selection
                text = read_direct()
                if text and text.strip():
                    if now - state["last_key"] > 0.3:
                        state["last_key"] = now
                        jobs.put("send")
                    listener.suppress_event()
            return

        if armed():                              # M only right after a selection
            if now - state["last_key"] > 0.3:
                state["last_key"] = now
                jobs.put("clear")
            listener.suppress_event()
            return

    if vk not in MODIFIER_VKS:
        state["armed_at"] = 0.0                  # you typed something: disarm


listener = keyboard.Listener(win32_event_filter=key_filter)

if __name__ == "__main__":
    print("Running. Select text, then: X = add it to the phone, M = clear all. F8 pauses.")
    print("Target:", ESP)
    threading.Thread(target=worker, daemon=True).start()
    mouse.Listener(on_click=on_click).start()
    listener.start()
    try:
        listener.join()
    except KeyboardInterrupt:
        print("Stopped.")

       