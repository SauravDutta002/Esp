# # # """
# # # Select text with the mouse, then press X to send it to the ESP32 (phone).

# # # Method 1: read the selection directly from the app (UI Automation). No keys, no clipboard.
# # # Method 2: if the app doesn't expose it, the script copies the selection itself
# # #           (only right after a mouse selection) and restores your old clipboard.

# # # X is swallowed only when a selection is being sent. Otherwise X types normally.
# # # Ctrl/Alt/Win + X are never touched.
# # # F8 = pause / resume.   Stop the script with Ctrl+C in this terminal.
# # # """
# # # import ctypes
# # # import math
# # # import queue
# # # import threading
# # # import time

# # # import pyperclip
# # # import requests
# # # import uiautomation as auto
# # # from pynput import keyboard, mouse

# # # ESP = "http://10.254.65.150"       # the ESP32's fixed address
# # # SEND_VK = 0x58                     # X key. Other keys: F9 = 0x78, Z = 0x5A
# # # DRAG_PX = 8                        # movement that counts as a drag-select
# # # MULTI_CLICK_S = 0.45               # double / triple-click window
# # # ARMED_FOR_S = 30                   # fallback copy is allowed this long after a selection
# # # MAX_CHARS = 20000
# # # TERMINALS = ("ConsoleWindowClass", "CASCADIA_HOSTING_WINDOW_CLASS")

# # # user32 = ctypes.windll.user32
# # # kb = keyboard.Controller()
# # # jobs = queue.Queue()
# # # state = {
# # #     "enabled": True,
# # #     "press": (0, 0),
# # #     "last_up": 0.0,
# # #     "armed_at": 0.0,               # time of the last mouse selection, 0 = none
# # #     "last_key": 0.0,
# # # }
# # # MODIFIER_VKS = {0x10, 0x11, 0x12, 0xA0, 0xA1, 0xA2, 0xA3, 0xA4, 0xA5, 0x5B, 0x5C}


# # # def held(vk):
# # #     return (user32.GetAsyncKeyState(vk) & 0x8000) != 0


# # # def foreground_class():
# # #     buf = ctypes.create_unicode_buffer(256)
# # #     user32.GetClassNameW(user32.GetForegroundWindow(), buf, 256)
# # #     return buf.value


# # # def armed():
# # #     return state["armed_at"] and (time.time() - state["armed_at"]) < ARMED_FOR_S


# # # # ---------- Method 1: direct read ----------
# # # def read_direct():
# # #     try:
# # #         with auto.UIAutomationInitializerInThread():
# # #             ctrl = auto.GetFocusedControl()
# # #             for _ in range(4):                   # browsers expose text on a parent
# # #                 if ctrl is None:
# # #                     break
# # #                 try:
# # #                     pattern = ctrl.GetTextPattern()
# # #                 except Exception:
# # #                     pattern = None
# # #                 if pattern:
# # #                     text = "".join(r.GetText(MAX_CHARS) for r in pattern.GetSelection())
# # #                     if text:
# # #                         return text
# # #                 ctrl = ctrl.GetParentControl()
# # #     except Exception:
# # #         pass
# # #     return None


# # # # ---------- Method 2: script copies the selection ----------
# # # def read_by_copy():
# # #     if foreground_class() in TERMINALS:          # Ctrl+C would interrupt a running program
# # #         return None
# # #     try:
# # #         old = pyperclip.paste()
# # #     except Exception:
# # #         old = None
# # #     try:
# # #         pyperclip.copy("")
# # #     except Exception:
# # #         return None

# # #     time.sleep(0.05)
# # #     with kb.pressed(keyboard.Key.ctrl):
# # #         kb.press("c")
# # #         kb.release("c")

# # #     text = ""
# # #     for _ in range(12):                          # wait up to ~0.6 s for the copy
# # #         time.sleep(0.05)
# # #         try:
# # #             text = pyperclip.paste()
# # #         except Exception:
# # #             text = ""
# # #         if text:
# # #             break

# # #     if old is not None:
# # #         try:
# # #             pyperclip.copy(old)                  # put the previous clipboard back
# # #         except Exception:
# # #             pass
# # #     return text or None


# # # def send(text, how):
# # #     try:
# # #         r = requests.post(ESP + "/p", data={"t": text[:MAX_CHARS]}, timeout=5)
# # #         print(f"Sent {len(text)} chars ({how}) -> {r.status_code}")
# # #     except Exception as e:
# # #         print("Send failed:", e)


# # # def worker():
# # #     while True:
# # #         jobs.get()
# # #         while not jobs.empty():                  # collapse repeats into one job
# # #             jobs.get_nowait()
# # #         time.sleep(0.05)
# # #         text = read_direct()
# # #         how = "direct read"
# # #         if not text and armed():
# # #             text = read_by_copy()
# # #             how = "copied by script"
# # #         if text and text.strip():
# # #             send(text, how)
# # #         else:
# # #             print("Nothing to send (no selection found).")


# # # # ---------- mouse: arm the fallback after a selection ----------
# # # def on_click(x, y, button, pressed):
# # #     if button != mouse.Button.left:
# # #         return
# # #     now = time.time()
# # #     if pressed:
# # #         state["press"] = (x, y)
# # #         return
# # #     dragged = math.hypot(x - state["press"][0], y - state["press"][1]) > DRAG_PX
# # #     multi = (now - state["last_up"]) < MULTI_CLICK_S
# # #     state["last_up"] = now
# # #     state["armed_at"] = now if (dragged or multi) else 0.0


# # # # ---------- keyboard: runs before Windows delivers each key ----------
# # # def key_filter(msg, data):
# # #     if msg not in (0x100, 0x104):                # key down / sys key down
# # #         return
# # #     if data.flags & 0x10:                        # ignore keys sent by this script
# # #         return

# # #     vk = data.vkCode
# # #     if vk == 0x77:                               # F8 toggles pause
# # #         state["enabled"] = not state["enabled"]
# # #         print("Send key:", "ON" if state["enabled"] else "PAUSED")
# # #         return

# # #     if vk != SEND_VK:
# # #         if vk not in MODIFIER_VKS:
# # #             state["armed_at"] = 0.0              # you typed something: disarm the fallback
# # #         return
# # #     if not state["enabled"]:
# # #         return
# # #     if held(0x11) or held(0x12) or held(0x5B) or held(0x5C):   # Ctrl / Alt / Win
# # #         return

# # #     now = time.time()
# # #     if armed():
# # #         # You just selected with the mouse: X means "send". Swallow it.
# # #         if now - state["last_key"] > 0.3:        # ignore key auto-repeat
# # #             state["last_key"] = now
# # #             jobs.put(1)
# # #         listener.suppress_event()
# # #         return

# # #     # Not armed (for example a keyboard selection): send only if the app exposes it
# # #     text = read_direct()
# # #     if text and text.strip():
# # #         if now - state["last_key"] > 0.3:
# # #             state["last_key"] = now
# # #             send(text, "direct read")
# # #         listener.suppress_event()
# # #     # otherwise X types normally


# # # listener = keyboard.Listener(win32_event_filter=key_filter)

# # # if __name__ == "__main__":
# # #     print(f"Running. Select text with the mouse, then press X. F8 pauses. Target: {ESP}")
# # #     threading.Thread(target=worker, daemon=True).start()
# # #     mouse.Listener(on_click=on_click).start()
# # #     listener.start()
# # #     try:
# # #         listener.join()
# # #     except KeyboardInterrupt:
# # #         print("Stopped.")





# # """
# # Select text with the mouse, then use these keys (right after selecting):

# #   X   add the selection to the text on the phone (always adds, never replaces)
# #   M   delete everything (clears the phone and the stored text)
# #   F8  pause / resume the whole script

# # The keys are swallowed only right after a mouse selection. With nothing
# # selected they type normally. Shift/Ctrl/Alt/Win + key is never touched.

# # Method 1: read the selection directly from the app (UI Automation).
# # Method 2: if the app doesn't expose it, the script copies it itself and
# #           restores your old clipboard.
# # Stop the script with Ctrl+C in this terminal.
# # """
# # import ctypes
# # import math
# # import queue
# # import threading
# # import time

# # import pyperclip
# # import requests
# # import uiautomation as auto
# # from pynput import keyboard, mouse

# # # Tried in order. Add the IP the ESP32 prints in the Serial Monitor.
# # ESP_LIST = ["http://10.254.65.150", "http://paste.local"]
# # ESP = ESP_LIST[0]

# # VK_SEND, VK_CLEAR = 0x58, 0x4D     # X, M
# # DRAG_PX = 8                        # movement that counts as a drag-select
# # MULTI_CLICK_S = 0.45               # double / triple-click window
# # ARMED_FOR_S = 30                   # keys work this long after a selection
# # MAX_CHARS = 20000
# # SEPARATOR = "\n"                   # placed between added selections
# # TERMINALS = ("ConsoleWindowClass", "CASCADIA_HOSTING_WINDOW_CLASS")

# # user32 = ctypes.windll.user32
# # kb = keyboard.Controller()
# # jobs = queue.Queue()
# # state = {
# #     "enabled": True,
# #     "buffer": "",                  # everything sent so far
# #     "last_piece": "",
# #     "press": (0, 0),
# #     "last_up": 0.0,
# #     "armed_at": 0.0,
# #     "last_key": 0.0,
# # }
# # MODIFIER_VKS = {0x10, 0x11, 0x12, 0xA0, 0xA1, 0xA2, 0xA3, 0xA4, 0xA5, 0x5B, 0x5C}


# # def held(vk):
# #     return (user32.GetAsyncKeyState(vk) & 0x8000) != 0


# # def any_modifier():
# #     return held(0x10) or held(0x11) or held(0x12) or held(0x5B) or held(0x5C)


# # def foreground_class():
# #     buf = ctypes.create_unicode_buffer(256)
# #     user32.GetClassNameW(user32.GetForegroundWindow(), buf, 256)
# #     return buf.value


# # def armed():
# #     return state["armed_at"] and (time.time() - state["armed_at"]) < ARMED_FOR_S


# # # ---------- talking to the ESP32 ----------
# # def post_esp(data):
# #     """Try the last working address first, then the others."""
# #     global ESP
# #     order = [ESP] + [a for a in ESP_LIST if a != ESP]
# #     for addr in order:
# #         try:
# #             r = requests.post(addr + "/p", data=data, timeout=2)
# #             ESP = addr
# #             return r.status_code
# #         except Exception:
# #             continue
# #     return None


# # # ---------- Method 1: direct read ----------
# # def read_direct():
# #     try:
# #         with auto.UIAutomationInitializerInThread():
# #             ctrl = auto.GetFocusedControl()
# #             for _ in range(4):                   # browsers expose text on a parent
# #                 if ctrl is None:
# #                     break
# #                 try:
# #                     pattern = ctrl.GetTextPattern()
# #                 except Exception:
# #                     pattern = None
# #                 if pattern:
# #                     text = "".join(r.GetText(MAX_CHARS) for r in pattern.GetSelection())
# #                     if text:
# #                         return text
# #                 ctrl = ctrl.GetParentControl()
# #     except Exception:
# #         pass
# #     return None


# # # ---------- Method 2: script copies the selection ----------
# # def read_by_copy():
# #     if foreground_class() in TERMINALS:          # Ctrl+C would interrupt a running program
# #         return None
# #     try:
# #         old = pyperclip.paste()
# #     except Exception:
# #         old = None
# #     try:
# #         pyperclip.copy("")
# #     except Exception:
# #         return None

# #     time.sleep(0.05)
# #     with kb.pressed(keyboard.Key.ctrl):
# #         kb.press("c")
# #         kb.release("c")

# #     text = ""
# #     for _ in range(12):                          # wait up to ~0.6 s for the copy
# #         time.sleep(0.05)
# #         try:
# #             text = pyperclip.paste()
# #         except Exception:
# #             text = ""
# #         if text:
# #             break

# #     if old is not None:
# #         try:
# #             pyperclip.copy(old)                  # put the previous clipboard back
# #         except Exception:
# #             pass
# #     return text or None


# # # ---------- actions ----------
# # def do_send():
# #     text = read_direct()
# #     how = "direct read"
# #     if not text and armed():
# #         text = read_by_copy()
# #         how = "copied by script"
# #     if not text or not text.strip():
# #         print("Nothing to send (no selection found).")
# #         return

# #     if text == state["last_piece"]:
# #         print("Same text as the last one, not added twice.")
# #         return

# #     if state["buffer"]:
# #         state["buffer"] = (state["buffer"] + SEPARATOR + text)[:MAX_CHARS]
# #     else:
# #         state["buffer"] = text[:MAX_CHARS]
# #     state["last_piece"] = text

# #     code = post_esp({"t": state["buffer"]})
# #     if code is None:
# #         print("Send failed: ESP32 not reachable. Is the laptop on the 'IOT' hotspot?")
# #     else:
# #         print(f"ADDED {len(text)} chars ({how}); phone now has {len(state['buffer'])} chars -> {code}")


# # def do_clear():
# #     state["buffer"] = ""
# #     state["last_piece"] = ""
# #     code = post_esp({"clear": "1"})
# #     print("CLEARED everything." if code else "Clear failed: ESP32 not reachable.")


# # def worker():
# #     while True:
# #         job = jobs.get()
# #         while not jobs.empty():                  # collapse repeats into one job
# #             job = jobs.get_nowait()
# #         time.sleep(0.05)
# #         if job == "send":
# #             do_send()
# #         elif job == "clear":
# #             do_clear()


# # # ---------- mouse: arm the keys after a selection ----------
# # def on_click(x, y, button, pressed):
# #     if button != mouse.Button.left:
# #         return
# #     now = time.time()
# #     if pressed:
# #         state["press"] = (x, y)
# #         return
# #     dragged = math.hypot(x - state["press"][0], y - state["press"][1]) > DRAG_PX
# #     multi = (now - state["last_up"]) < MULTI_CLICK_S
# #     state["last_up"] = now
# #     state["armed_at"] = now if (dragged or multi) else 0.0


# # # ---------- keyboard: runs before Windows delivers each key ----------
# # def key_filter(msg, data):
# #     if msg not in (0x100, 0x104):                # key down / sys key down
# #         return
# #     if data.flags & 0x10:                        # ignore keys sent by this script
# #         return

# #     vk = data.vkCode
# #     now = time.time()

# #     if vk == 0x77:                               # F8 toggles pause
# #         state["enabled"] = not state["enabled"]
# #         print("Keys:", "ON" if state["enabled"] else "PAUSED")
# #         return

# #     if vk in (VK_SEND, VK_CLEAR) and state["enabled"] and not any_modifier():
# #         if vk == VK_SEND:
# #             if armed():
# #                 if now - state["last_key"] > 0.3:    # ignore key auto-repeat
# #                     state["last_key"] = now
# #                     jobs.put("send")
# #                 listener.suppress_event()
# #             else:
# #                 # no mouse selection: send only if the app exposes a selection
# #                 text = read_direct()
# #                 if text and text.strip():
# #                     if now - state["last_key"] > 0.3:
# #                         state["last_key"] = now
# #                         jobs.put("send")
# #                     listener.suppress_event()
# #             return

# #         if armed():                              # M only right after a selection
# #             if now - state["last_key"] > 0.3:
# #                 state["last_key"] = now
# #                 jobs.put("clear")
# #             listener.suppress_event()
# #             return

# #     if vk not in MODIFIER_VKS:
# #         state["armed_at"] = 0.0                  # you typed something: disarm


# # listener = keyboard.Listener(win32_event_filter=key_filter)

# # if __name__ == "__main__":
# #     print("Running. Select text, then: X = add it to the phone, M = clear all. F8 pauses.")
# #     print("Target:", ESP)
# #     threading.Thread(target=worker, daemon=True).start()
# #     mouse.Listener(on_click=on_click).start()
# #     listener.start()
# #     try:
# #         listener.join()
# #     except KeyboardInterrupt:
# #         print("Stopped.")

       


# """
# Select text with the mouse, then press (right after selecting):

#   X    add the selection to the phone
#   G    add the selection (if new) and send everything to Gemini.
#        The reply is typed on the laptop through the ESP32.
#   M    delete everything (phone box and stored text)

# Works any time:
#   F9   STOP typing / cancel the Gemini request
#   F8   pause / resume the keys above

# X, G and M are swallowed only right after a mouse selection. With nothing
# selected they type normally. Shift/Ctrl/Alt/Win + key is never touched.

# Method 1: read the selection directly from the app (UI Automation).
# Method 2: if the app doesn't expose it, the script copies it itself and
#           restores your old clipboard.
# Stop the script with Ctrl+C in this terminal.
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

# # Tried in order. Add the IP the ESP32 prints in the Serial Monitor.
# ESP_LIST = ["http://10.254.65.150", "http://paste.local"]
# ESP = ESP_LIST[0]

# VK_SEND, VK_CLEAR, VK_ASK = 0x58, 0x4D, 0x47     # X, M, G
# VK_STOP = 0x78                                   # F9  (F10 = 0x79, Pause = 0x13)
# VK_PAUSE = 0x77                                  # F8
# DRAG_PX = 8                        # movement that counts as a drag-select
# MULTI_CLICK_S = 0.45               # double / triple-click window
# ARMED_FOR_S = 30                   # keys work this long after a selection
# ARMED_AFTER_KEY_S = 12             # after X or M, the keys stay active this much longer
# MAX_CHARS = 20000
# SEPARATOR = "\n"                   # placed between added selections
# TERMINALS = ("ConsoleWindowClass", "CASCADIA_HOSTING_WINDOW_CLASS")

# user32 = ctypes.windll.user32
# kb = keyboard.Controller()
# jobs = queue.Queue()
# state = {
#     "enabled": True,
#     "buffer": "",                  # everything sent so far
#     "last_piece": "",
#     "press": (0, 0),
#     "last_up": 0.0,
#     "armed_at": 0.0,
#     "last_key": 0.0,
# }
# MODIFIER_VKS = {0x10, 0x11, 0x12, 0xA0, 0xA1, 0xA2, 0xA3, 0xA4, 0xA5, 0x5B, 0x5C}


# def held(vk):
#     return (user32.GetAsyncKeyState(vk) & 0x8000) != 0


# def any_modifier():
#     return held(0x10) or held(0x11) or held(0x12) or held(0x5B) or held(0x5C)


# def foreground_class():
#     buf = ctypes.create_unicode_buffer(256)
#     user32.GetClassNameW(user32.GetForegroundWindow(), buf, 256)
#     return buf.value


# def armed():
#     return state["armed_at"] and (time.time() - state["armed_at"]) < ARMED_FOR_S


# def shorten_armed():
#     """After X or M, keep the keys active only a little longer."""
#     state["armed_at"] = min(state["armed_at"], time.time() - (ARMED_FOR_S - ARMED_AFTER_KEY_S))


# # ---------- talking to the ESP32 ----------
# def post_esp(data):
#     """Try the last working address first, then the others."""
#     global ESP
#     order = [ESP] + [a for a in ESP_LIST if a != ESP]
#     for addr in order:
#         try:
#             r = requests.post(addr + "/p", data=data, timeout=2)
#             ESP = addr
#             return r.status_code
#         except Exception:
#             continue
#     return None


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


# # ---------- actions ----------
# def do_send():
#     """Add the current selection to the phone. Returns False only on a hard failure."""
#     text = read_direct()
#     how = "direct read"
#     if not text and armed():
#         text = read_by_copy()
#         how = "copied by script"
#     if not text or not text.strip():
#         print("Nothing new to add (no selection found).")
#         return True

#     if text == state["last_piece"]:
#         print("Same text as the last one, not added twice.")
#         return True

#     if state["buffer"]:
#         state["buffer"] = (state["buffer"] + SEPARATOR + text)[:MAX_CHARS]
#     else:
#         state["buffer"] = text[:MAX_CHARS]
#     state["last_piece"] = text

#     code = post_esp({"t": state["buffer"]})
#     if code is None:
#         print("Send failed: ESP32 not reachable. Is the laptop on the 'IOT' hotspot?")
#         return False
#     print(f"ADDED {len(text)} chars ({how}); phone now has {len(state['buffer'])} chars -> {code}")
#     return True


# def do_ask():
#     ok = do_send()                               # make sure the latest selection is included
#     if not ok:
#         return
#     code = post_esp({"ask": "1"})
#     print("ASKED Gemini." if code else "Ask failed: ESP32 not reachable.")


# def do_clear():
#     state["buffer"] = ""
#     state["last_piece"] = ""
#     code = post_esp({"clear": "1"})
#     print("CLEARED everything." if code else "Clear failed: ESP32 not reachable.")


# def do_stop():
#     code = post_esp({"stop": "1"})
#     print("STOP sent." if code else "Stop failed: ESP32 not reachable.")


# def worker():
#     while True:
#         job = jobs.get()
#         time.sleep(0.05)
#         if job == "send":
#             do_send()
#         elif job == "ask":
#             do_ask()
#         elif job == "clear":
#             do_clear()


# # ---------- mouse: arm the keys after a selection ----------
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
#     now = time.time()

#     if vk == VK_STOP:                            # works any time, even while paused
#         threading.Thread(target=do_stop, daemon=True).start()
#         listener.suppress_event()
#         return

#     if vk == VK_PAUSE:                           # F8 toggles pause
#         state["enabled"] = not state["enabled"]
#         print("Keys:", "ON" if state["enabled"] else "PAUSED")
#         return

#     if vk in (VK_SEND, VK_ASK, VK_CLEAR) and state["enabled"] and not any_modifier():
#         if vk == VK_SEND:
#             if armed():
#                 if now - state["last_key"] > 0.3:    # ignore key auto-repeat
#                     state["last_key"] = now
#                     jobs.put("send")
#                     shorten_armed()
#                 listener.suppress_event()
#             else:
#                 # no mouse selection: send only if the app exposes a selection
#                 text = read_direct()
#                 if text and text.strip():
#                     if now - state["last_key"] > 0.3:
#                         state["last_key"] = now
#                         jobs.put("send")
#                     listener.suppress_event()
#             return

#         if armed():                              # G and M only right after a selection
#             if now - state["last_key"] > 0.3:
#                 state["last_key"] = now
#                 if vk == VK_ASK:
#                     jobs.put("ask")
#                     state["armed_at"] = 0.0      # disarm: the reply will be typed next
#                 else:
#                     jobs.put("clear")
#                     shorten_armed()
#             listener.suppress_event()
#             return

#     if vk not in MODIFIER_VKS:
#         state["armed_at"] = 0.0                  # you typed something: disarm


# listener = keyboard.Listener(win32_event_filter=key_filter)

# if __name__ == "__main__":
#     print("Running. Select text, then: X = add, G = ask Gemini, M = clear.")
#     print("F9 = STOP typing (any time).  F8 = pause keys.")
#     print("Target:", ESP)
#     threading.Thread(target=worker, daemon=True).start()
#     mouse.Listener(on_click=on_click).start()
#     listener.start()
#     try:
#         listener.join()
#     except KeyboardInterrupt:
#         print("Stopped.")


# ////////////// above wokrking



"""
Select text with the mouse, then press (right after selecting):

  X    add the selection to the phone
  G    add the selection (if new) and ask the AI (Gemini or Claude, chosen on
       the phone). The reply is typed on the laptop through the ESP32.
       The AI remembers earlier questions until you press M.
  M    start a NEW chat (clears the phone box and the AI's memory)
  O    force "screen read": send what is on screen where you dragged (OCR).
       Use it when a site gives back junk like "copying is not allowed".

Works any time:
  F9   STOP typing / cancel the AI request
  F8   pause / resume the keys above

X, G, M and O are swallowed only right after a mouse selection. With nothing
selected they type normally. Shift/Ctrl/Alt/Win + key is never touched.

Method 1: read the selection directly from the app (UI Automation).
Method 2: if the app doesn't expose it, the script copies it itself and
          restores your old clipboard.
Method 3: UNIVERSAL COPY. If copying is blocked (copy-protected sites,
          user-select:none, PDFs, images, videos, apps that refuse Ctrl+C),
          the script reads the text straight off the screen with the OCR
          built into Windows, using where you dragged / double / triple-clicked.
          Just drag over the text the normal way, even if nothing highlights.
Stop the script with Ctrl+C in this terminal.

Needs once:  pip install "winrt-Windows.Media.Ocr" "winrt-Windows.Graphics.Imaging"
             "winrt-Windows.Storage.Streams" "winrt-Windows.Foundation" pillow
"""
import asyncio
import ctypes
import math
import queue
import threading
import time
from ctypes import wintypes

try:                                            # real pixels, so mouse == screenshot coordinates
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass

import pyperclip
import requests
import uiautomation as auto
from pynput import keyboard, mouse

try:
    from PIL import Image, ImageGrab
    from winrt.windows.graphics.imaging import BitmapAlphaMode, BitmapPixelFormat, SoftwareBitmap
    from winrt.windows.media.ocr import OcrEngine
    from winrt.windows.storage.streams import DataWriter
    OCR_OK = True
except Exception as e:                          # script still works, just without method 3
    print("Screen read (OCR) disabled:", e)
    OCR_OK = False

import socket

ESP_HOST = 150                     # must match STATIC_HOST in esp.ino


def guess_esp():
    """The ESP32 sits at <same subnet as this laptop>.150, so work it out from our own IP.
    (Phone hotspots change their subnet now and then.) No data is actually sent."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
        return "http://" + ".".join(ip.split(".")[:3] + [str(ESP_HOST)])
    except Exception:
        return None


# Tried in order. The auto-guessed address comes first; add any fixed IP here too.
ESP_LIST = [a for a in (guess_esp(), "http://paste.local") if a]
ESP = ESP_LIST[0]

VK_SEND, VK_CLEAR, VK_ASK = 0x58, 0x4D, 0x47     # X, M, G
VK_OCR = 0x4F                                    # O
VK_STOP = 0x78                                   # F9  (F10 = 0x79, Pause = 0x13)
VK_PAUSE = 0x77                                  # F8
DRAG_PX = 8                        # movement that counts as a drag-select
MULTI_CLICK_S = 0.45               # double / triple-click window
ARMED_FOR_S = 30                   # keys work this long after a selection
ARMED_AFTER_KEY_S = 12             # after X or M, the keys stay active this much longer
MAX_CHARS = 20000
SEPARATOR = "\n"                   # placed between added selections
TERMINALS = ("ConsoleWindowClass", "CASCADIA_HOSTING_WINDOW_CLASS")
OCR_PAD = 40                       # px above/below the drag that is read from the screen
OCR_SCALE = 2                      # enlarge the screenshot first: small text reads better

user32 = ctypes.windll.user32
user32.GetForegroundWindow.restype = wintypes.HWND
user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
kb = keyboard.Controller()
jobs = queue.Queue()
state = {
    "enabled": True,
    "buffer": "",                  # everything sent so far
    "last_piece": "",
    "press": (0, 0),
    "last_up": 0.0,
    "clicks": 0,                   # 2 = double-click, 3 = triple-click
    "sel": None,                   # where the last mouse selection was on screen
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


def shorten_armed():
    """After X or M, keep the keys active only a little longer."""
    state["armed_at"] = min(state["armed_at"], time.time() - (ARMED_FOR_S - ARMED_AFTER_KEY_S))


# ---------- talking to the ESP32 ----------
def post_esp(data, timeout=2):
    """Try the last working address first, then the others.
    Returns the HTTP status code, or None if the ESP32 could not be reached."""
    global ESP
    fresh = guess_esp()                          # the hotspot may have changed subnet
    if fresh and fresh not in ESP_LIST:
        ESP_LIST.insert(0, fresh)
        print("Network changed, now trying", fresh)
    order = [ESP] + [a for a in ESP_LIST if a != ESP]
    for addr in order:
        try:
            r = requests.post(addr + "/p", data=data, timeout=timeout)
            ESP = addr
            return r.status_code
        except Exception:
            continue
    return None


def ok(code):
    return code == 200


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
    """Screenshot a box and OCR it.
    Returns lines (top to bottom); each line is a list of (text, x0, y0, x1, y1) in screen pixels."""
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
    """Read the text the mouse went over, straight from the pixels.
    Works even where copying is blocked."""
    if not OCR_OK or not sel:
        return None
    try:
        x1, y1, x2, y2, clicks = sel
        wl, wt, wr, wb = foreground_rect()
        if wr - wl < 50 or wb - wt < 20:        # weird window: use a wide strip instead
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

        # drag: from the start point to the end point, in reading order
        a, b = (x1, y1), (x2, y2)
        ia, ib = _nearest_line(lines, *a), _nearest_line(lines, *b)
        if ia > ib or (ia == ib and a[0] > b[0]):
            a, b, ia, ib = b, a, ib, ia
        if ia == ib:
            return _join([w for w in lines[ia] if w[3] > a[0] and w[1] < b[0]])

        # several lines: only keep lines in the same column as the selection
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
                return text, "copied by script"
    if fallback or force_ocr:
        text = read_by_ocr(sel)
        if text and text.strip():
            return text, "read from screen (OCR)"
    return None, ""


# ---------- actions ----------
def do_send(fallback=True, force_ocr=False):
    """Add the current selection to the phone. Returns False only on a hard failure."""
    text, how = get_selection(fallback, force_ocr)
    if not text:
        print("Nothing new to add (no selection found).")
        return True

    if text == state["last_piece"]:
        print("Same text as the last one, not added twice.")
        return True

    if state["buffer"]:
        state["buffer"] = (state["buffer"] + SEPARATOR + text)[:MAX_CHARS]
    else:
        state["buffer"] = text[:MAX_CHARS]
    state["last_piece"] = text

    code = post_esp({"t": state["buffer"]})
    if code is None:
        print("Send failed: ESP32 not reachable. Is the laptop on the 'IOT' hotspot?")
        return False
    if not ok(code):
        print(f"Send failed: ESP32 answered {code}.")
        return False
    print(f"ADDED {len(text)} chars ({how}); phone now has {len(state['buffer'])} chars")
    return True


def do_ask(fallback=True):
    if not do_send(fallback):                    # make sure the latest selection is included
        return
    code = post_esp({"ask": "1"})
    if ok(code):
        # The question is on its way. The next X starts a new question; the AI
        # still remembers this one (press M for a fresh chat).
        state["buffer"] = ""
        print("ASKED the AI. Next selection starts a new question.")
    elif code is None:
        print("Ask failed: ESP32 not reachable.")
    else:
        print(f"Ask failed: ESP32 answered {code}. Flash the new esp.ino.")


def do_newchat():
    state["buffer"] = ""
    state["last_piece"] = ""
    post_esp({"clear": "1"})                     # empty the phone's selected-text box
    code = post_esp({"newchat": "1"})            # wipe the AI's memory on the phone
    if ok(code):
        print("NEW CHAT started.")
    else:
        print("New chat failed:", "ESP32 not reachable." if code is None else f"ESP32 answered {code}.")


def do_stop():
    # The ESP32 can be busy typing a chunk for a few seconds, so wait longer here.
    code = post_esp({"stop": "1"}, timeout=8)
    if ok(code):
        print("STOP sent.")
    else:
        print("Stop failed:", "ESP32 not reachable." if code is None else f"ESP32 answered {code}.")


def worker():
    while True:
        job, fallback = jobs.get()               # fallback = there was a mouse selection
        time.sleep(0.05)
        try:
            if job == "send":
                do_send(fallback)
            elif job == "ocr":
                do_send(fallback, force_ocr=True)
            elif job == "ask":
                do_ask(fallback)
            elif job == "newchat":
                do_newchat()
        except Exception as e:
            print("Error:", e)


# ---------- mouse: arm the keys after a selection ----------
def on_click(x, y, button, pressed):
    if button != mouse.Button.left:
        return
    now = time.time()
    if pressed:
        state["press"] = (x, y)
        return
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


# ---------- keyboard: runs before Windows delivers each key ----------
def key_filter(msg, data):
    if msg not in (0x100, 0x104):                # key down / sys key down
        return
    if data.flags & 0x10:                        # ignore keys sent by this script
        return

    vk = data.vkCode
    now = time.time()

    if vk == VK_STOP:                            # works any time, even while paused
        threading.Thread(target=do_stop, daemon=True).start()
        listener.suppress_event()
        return

    if vk == VK_PAUSE:                           # F8 toggles pause
        state["enabled"] = not state["enabled"]
        print("Keys:", "ON" if state["enabled"] else "PAUSED")
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
                    state["armed_at"] = 0.0      # disarm: the reply will be typed next
                elif vk == VK_OCR:
                    jobs.put(("ocr", True))
                    shorten_armed()
                else:
                    jobs.put(("newchat", True))
                    shorten_armed()
            listener.suppress_event()
            return

    if vk not in MODIFIER_VKS:
        state["armed_at"] = 0.0                  # you typed something: disarm


listener = keyboard.Listener(win32_event_filter=key_filter)

if __name__ == "__main__":
    print("Running. Select text, then: X = add, G = ask AI, M = new chat, O = force screen read.")
    print("F9 = STOP typing (any time).  F8 = pause keys.")
    print("Universal copy (OCR):", "ON" if OCR_OK else "OFF")
    print("Target:", ESP)
    threading.Thread(target=worker, daemon=True).start()
    mouse.Listener(on_click=on_click, on_scroll=on_scroll).start()
    listener.start()
    try:
        listener.join()
    except KeyboardInterrupt:
        print("Stopped.")