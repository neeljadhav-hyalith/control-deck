"""Control Deck - turn a spare phone into a control deck for a Windows laptop, on the same Wi-Fi.

Run:  py server.py   (first run creates config.json from config.example.json)
The phone sends an action id from config.json, never a command.
"""
import json
import os
import re
import secrets
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, unquote

BASE = os.path.dirname(os.path.abspath(__file__))
STATIC = os.path.join(BASE, "static")
ICON_DIR = os.path.join(STATIC, "icons")
CFG_PATH = os.path.join(BASE, "config.json")
WINDOWS = os.name == "nt"


CFG_LOCK = threading.Lock()


def save_cfg(c):
    with CFG_LOCK:
        tmp = CFG_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(c, f, indent=2, ensure_ascii=False)
        os.replace(tmp, CFG_PATH)


def load_cfg():
    if not os.path.exists(CFG_PATH):  # first run: start from the example
        import shutil
        shutil.copy(os.path.join(BASE, "config.example.json"), CFG_PATH)
    with open(CFG_PATH, encoding="utf-8") as f:
        c = json.load(f)
    changed = False
    if not c.get("pin"):
        c["pin"] = f"{secrets.randbelow(10**6):06d}"
        changed = True
    if not c.get("hook_secret"):
        c["hook_secret"] = secrets.token_hex(16)
        changed = True
    if changed:
        save_cfg(c)
    return c


cfg = load_cfg()
ACTIONS = {a["id"]: a for a in cfg["actions"] if "id" in a}

# Tiles the phone may add. The phone picks one of these or a WhatsApp chat; it can never define a command.
PRESETS = {
    "dictate": {"id": "dictate", "title": "Dictate", "sub": "Wispr Flow", "glyph": "wave", "icon": "wispr",
                "type": "keys", "keys": ["ctrl", "win", "space"], "toggle": True},
    "shot": {"id": "shot", "title": "Screenshot", "sub": "Full screen", "glyph": "shot", "type": "keys", "keys": ["win", "prtsc"]},
    "snip": {"id": "snip", "title": "Snip", "sub": "Pick an area", "glyph": "snip", "type": "keys", "keys": ["win", "shift", "s"]},
    "lock": {"id": "lock", "title": "Lock", "sub": "Step away", "glyph": "lock", "type": "lock"},
    "meeting": {"id": "meeting", "title": "Meeting", "sub": "Join · volume 40%", "glyph": "video", "type": "meeting"},
    "focus25": {"id": "focus25", "title": "Focus 25", "sub": "Start a session", "glyph": "timer", "type": "focus", "minutes": 25},
    "psadmin": {"id": "psadmin", "title": "PowerShell", "sub": "As admin", "glyph": "terminal", "type": "admin_shell"},
    "away": {"id": "away", "title": "Away", "sub": "Pause · mute · lock", "glyph": "away", "type": "away"},
    "focus30": {"id": "focus30", "title": "Focus", "sub": "30 minutes", "glyph": "timer", "type": "focusmode", "minutes": 30},
    "camera": {"id": "camera", "title": "Camera", "sub": "On", "glyph": "camera", "type": "camera"},
    "desktop": {"id": "desktop", "title": "Desktop", "sub": "Show desktop", "glyph": "desktop", "type": "keys", "keys": ["win", "d"]},
    "eco": {"id": "eco", "title": "Eco", "sub": "Screens off · save power", "glyph": "eco", "type": "eco"},
    "approve": {"id": "approve", "title": "Claude", "sub": "Nothing waiting", "glyph": "check", "type": "claude_approve"},
    # opens an app on the phone itself; the laptop does nothing
    "mygate": {"id": "mygate", "title": "Mygate", "sub": "Open the app", "glyph": "gate", "type": "link", "url": "mygate://"},
}

# ---------------------------------------------------------------- keyboard
if WINDOWS:
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    ULONG_PTR = ctypes.c_size_t

    class KEYBDINPUT(ctypes.Structure):
        _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD),
                    ("time", wintypes.DWORD), ("dwExtraInfo", ULONG_PTR)]

    class MOUSEINPUT(ctypes.Structure):
        _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG), ("mouseData", wintypes.DWORD),
                    ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD), ("dwExtraInfo", ULONG_PTR)]

    class HARDWAREINPUT(ctypes.Structure):
        _fields_ = [("uMsg", wintypes.DWORD), ("wParamL", wintypes.WORD), ("wParamH", wintypes.WORD)]

    class _U(ctypes.Union):
        _fields_ = [("ki", KEYBDINPUT), ("mi", MOUSEINPUT), ("hi", HARDWAREINPUT)]

    class INPUT(ctypes.Structure):
        _fields_ = [("type", wintypes.DWORD), ("u", _U)]

    user32.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int]
    user32.SetForegroundWindow.argtypes = [wintypes.HWND]
    user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.IsIconic.argtypes = [wintypes.HWND]
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR,
                                                    ctypes.POINTER(wintypes.DWORD)]
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]

VK = {"win": 0x5B, "shift": 0x10, "ctrl": 0x11, "alt": 0x12, "esc": 0x1B, "enter": 0x0D, "tab": 0x09,
      "space": 0x20, "down": 0x28, "up": 0x26, "prtsc": 0x2C, "play": 0xB3, "next": 0xB0, "prev": 0xB1,
      "volup": 0xAF, "voldown": 0xAE, "volmute": 0xAD}
EXTENDED = {0x5B, 0x28, 0x26, 0x2C, 0xB3, 0xB0, 0xB1, 0xAF, 0xAE, 0xAD}


def _key(vk, up, unicode_ch=None):
    i = INPUT()
    i.type = 1
    if unicode_ch is not None:
        i.u.ki = KEYBDINPUT(0, unicode_ch, 4 | (2 if up else 0), 0, 0)
    else:
        i.u.ki = KEYBDINPUT(vk, 0, (1 if vk in EXTENDED else 0) | (2 if up else 0), 0, 0)
    return i


def _send(events):
    arr = (INPUT * len(events))(*events)
    user32.SendInput(len(events), arr, ctypes.sizeof(INPUT))


def press(*keys):
    if not WINDOWS:
        print("  [dry] keys", keys)
        return
    codes = [VK[k.lower()] if k.lower() in VK else ord(k.upper()) for k in keys]
    _send([_key(c, False) for c in codes] + [_key(c, True) for c in reversed(codes)])


def type_text(text):
    if not WINDOWS:
        print("  [dry] type", text)
        return
    units = text.encode("utf-16-le")
    events = []
    for n in range(0, len(units), 2):
        cu = int.from_bytes(units[n:n + 2], "little")
        events += [_key(0, False, cu), _key(0, True, cu)]
    _send(events)


def find_window(exe_contains):
    if not WINDOWS:
        return None
    found = []
    proc = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)

    def cb(hwnd, _):
        if not user32.IsWindowVisible(hwnd) or user32.GetWindowTextLengthW(hwnd) == 0:
            return True
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        h = kernel32.OpenProcess(0x1000, False, pid.value)
        if h:
            buf = ctypes.create_unicode_buffer(520)
            size = wintypes.DWORD(520)
            if kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
                if exe_contains.lower() in os.path.basename(buf.value).lower():
                    found.append(hwnd)
            kernel32.CloseHandle(h)
        return True

    user32.EnumWindows(proc(cb), 0)
    return found[0] if found else None


def focus(exe_contains):
    if not WINDOWS:
        print("  [dry] focus", exe_contains)
        return True
    hwnd = find_window(exe_contains)
    if not hwnd:
        return False
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, 9)
    press("alt")  # lets a background process take the foreground
    user32.SetForegroundWindow(hwnd)
    time.sleep(0.25)
    return True


def start(target):
    if not WINDOWS:
        print("  [dry] open", target)
        return True
    try:
        os.startfile(os.path.expandvars(target))
        return True
    except OSError:
        return False


# ---------------------------------------------------------------- real audio (pycaw), with key fallback
AUDIO = False
try:
    if WINDOWS:
        import comtypes
        from comtypes import CLSCTX_ALL
        from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume
        AUDIO = True
except Exception:
    AUDIO = False


def _endpoint(dev):
    if hasattr(dev, "EndpointVolume"):
        return dev.EndpointVolume
    iface = dev.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
    return ctypes.cast(iface, ctypes.POINTER(IAudioEndpointVolume))


_COM = threading.local()


def _audio(fn):
    if not getattr(_COM, "ready", False):
        try:
            comtypes.CoInitialize()
        except OSError:
            pass  # already initialised on this thread
        _COM.ready = True
    return fn()


def speakers():
    return _endpoint(AudioUtilities.GetSpeakers())


def microphone():
    return _endpoint(AudioUtilities.GetMicrophone())


def audio_state():
    if not AUDIO:
        return {"vol": None, "muted": None, "mic_muted": None, "audio": False} if WINDOWS else \
               {"vol": 0.42, "muted": False, "mic_muted": False, "audio": True}

    def read():
        out = {"audio": True}
        try:
            s = speakers()
            out["vol"] = round(s.GetMasterVolumeLevelScalar(), 3)
            out["muted"] = bool(s.GetMute())
        except Exception:
            out["vol"], out["muted"] = None, None
        try:
            out["mic_muted"] = bool(microphone().GetMute())
        except Exception:
            out["mic_muted"] = None
        return out
    return _audio(read)


def set_volume(v):
    v = max(0.0, min(1.0, float(v)))
    if not AUDIO:
        print("  [dry] volume", v)
        return True
    def go():
        s = speakers()
        s.SetMasterVolumeLevelScalar(v, None)
        if v > 0 and s.GetMute():
            s.SetMute(0, None)
    _audio(go)
    return True


def toggle_speaker_mute():
    if not AUDIO:
        press("volmute")
        return True
    _audio(lambda: speakers().SetMute(0 if speakers().GetMute() else 1, None))
    return True


def toggle_mic():
    if not AUDIO:
        press("win", "alt", "k")  # works inside calls that support Windows call mute
        return True
    _audio(lambda: microphone().SetMute(0 if microphone().GetMute() else 1, None))
    return True


# ---------------------------------------------------------------- native parts run in helper.py (crash-proof)
import base64
import subprocess
import sys as _sys
import importlib.util as _iu

_PY = _sys.executable or "python"
if _PY.lower().endswith("pythonw.exe"):
    _PY = _PY[:-len("pythonw.exe")] + "python.exe"
_HELPER = os.path.join(BASE, "helper.py")
_NOWIN = 0x08000000 if WINDOWS else 0  # CREATE_NO_WINDOW: no console flash


def helper(*args, timeout=10):
    try:
        r = subprocess.run([_PY, _HELPER, *args], capture_output=True, timeout=timeout, creationflags=_NOWIN)
        out = json.loads(r.stdout.decode("utf-8", "replace") or "{}")
        if r.returncode != 0 and not out:
            return {"error": f"helper exited {r.returncode}"}
        return out
    except subprocess.TimeoutExpired:
        return {"error": "helper timed out"}
    except Exception as e:
        return {"error": str(e)[:120]}


BRIGHT = (not WINDOWS) or _iu.find_spec("screen_brightness_control") is not None
MEDIA = WINDOWS and _iu.find_spec("winrt") is not None


class Bright:
    value = None if WINDOWS else 70
    target = None
    ev = threading.Event()
    err_shown = False


def bright_loop():
    if not WINDOWS or not BRIGHT:
        return
    while True:
        if Bright.target is None:
            r = helper("bright-get")
            if r.get("value") is not None:
                Bright.value = r["value"]
            elif r.get("error") and not Bright.err_shown:
                Bright.err_shown = True
                print("  display brightness:", r["error"])
        if Bright.ev.wait(10):
            Bright.ev.clear()
        while Bright.target is not None:
            t, Bright.target = Bright.target, None
            r = helper("bright-set", str(t))
            if r.get("error"):
                print("  display brightness:", r["error"])
            else:
                Bright.value = t


def set_brightness(v):
    if not BRIGHT:
        return False
    Bright.target = max(0, min(100, int(round(float(v) * 100))))
    Bright.value = Bright.target
    Bright.ev.set()
    return True


NOW = {"info": None, "art": None, "key": "", "fails": 0}


def media_loop():
    if not WINDOWS:
        NOW["info"] = {"title": "Blinding Lights", "artist": "The Weeknd", "album": "After Hours", "playing": True, "app": "Spotify"}
        return
    if not MEDIA:
        return
    while True:
        r = helper("media", timeout=8)
        if r.get("error"):
            NOW["fails"] += 1
            if NOW["fails"] in (1, 20):
                print("  now playing:", r["error"])
            time.sleep(min(30, 2 + NOW["fails"]))
            continue
        NOW["fails"] = 0
        info = r.get("info")
        NOW["info"] = info
        if info:
            key = info["title"] + "|" + info["artist"]
            if key != NOW["key"]:
                NOW["key"] = key
                NOW["art"] = base64.b64decode(r["art"]) if r.get("art") else None
        time.sleep(2)


# ---------------------------------------------------------------- next meeting (Google Calendar secret iCal link)
MEET = {"next": None, "err": None}
LINK_RE = re.compile(r"https://(?:meet\.google\.com|[\w.-]*zoom\.us|teams\.microsoft\.com|teams\.live\.com)/[^\s<>\"\\\\]+")


def calendar_loop():
    url = cfg.get("calendar_ics", "").strip()
    if not url:
        if not WINDOWS:
            MEET["next"] = {"title": "Weekly team sync", "start": int((time.time() + 12 * 60) * 1000),
                            "end": int((time.time() + 42 * 60) * 1000), "link": "https://meet.google.com/abc-defg-hij"}
        return
    try:
        import urllib.request
        import datetime as dt
        import icalendar
        import recurring_ical_events
    except Exception:
        MEET["err"] = "py -m pip install icalendar recurring-ical-events"
        return
    while True:
        try:
            data = urllib.request.urlopen(url, timeout=15).read()
            cal = icalendar.Calendar.from_ical(data)
            now = dt.datetime.now(dt.timezone.utc)
            evs = recurring_ical_events.of(cal).between(now - dt.timedelta(hours=2), now + dt.timedelta(days=2))
            best = None
            for e in evs:
                s, en = e.get("DTSTART").dt, (e.get("DTEND") or e.get("DTSTART")).dt
                if not isinstance(s, dt.datetime):
                    continue  # all-day
                s = s if s.tzinfo else s.astimezone()
                en = en if en.tzinfo else en.astimezone()
                if en <= now:
                    continue
                if best is None or s < best[0]:
                    blob = " ".join(str(e.get(k, "")) for k in ("X-GOOGLE-CONFERENCE", "LOCATION", "DESCRIPTION", "URL"))
                    m = LINK_RE.search(blob)
                    best = (s, en, str(e.get("SUMMARY", "Meeting")), m.group(0) if m else None)
            MEET["next"] = None if not best else {"title": best[2], "start": int(best[0].timestamp() * 1000),
                                                   "end": int(best[1].timestamp() * 1000), "link": best[3]}
            MEET["err"] = None
        except Exception as e:
            MEET["err"] = str(e)[:120]
        time.sleep(300)


# ---------------------------------------------------------------- focus timer and focus mode
FOCUS = {"end": 0, "minutes": 0, "mode": False, "toasts": None, "closed": []}
TOAST_KEY = r"Software\Microsoft\Windows\CurrentVersion\PushNotifications"


def _toasts(enable=None):
    """Read, or set, Windows' per-user switch for notification banners. Returns the previous value."""
    if not WINDOWS:
        return 1
    import winreg
    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, TOAST_KEY, 0, winreg.KEY_READ | winreg.KEY_SET_VALUE) as k:
        try:
            prev = winreg.QueryValueEx(k, "ToastEnabled")[0]
        except OSError:
            prev = 1
        if enable is not None:
            winreg.SetValueEx(k, "ToastEnabled", 0, winreg.REG_DWORD, 1 if enable else 0)
    return prev


def _running(exe):
    if not WINDOWS:
        return False
    r = subprocess.run(["tasklist", "/FI", f"IMAGENAME eq {exe}", "/NH"], capture_output=True, text=True, creationflags=_NOWIN)
    return exe.lower() in r.stdout.lower()


def focus_mode_start(minutes):
    if FOCUS["mode"]:
        return True, "already focusing"
    FOCUS.update(end=time.time() + minutes * 60, minutes=minutes, mode=True, closed=[])
    try:
        FOCUS["toasts"] = _toasts(False)
        cfg["_focus_restore_toasts"] = FOCUS["toasts"]  # survives a crash or restart mid-session
        save_cfg(cfg)
    except Exception as e:
        print("  focus: notifications", e)
    for app in cfg.get("focus_close", [{"exe": "WhatsApp.Root.exe", "reopen": "whatsapp:"},
                                        {"exe": "WhatsApp.exe", "reopen": "whatsapp:"}]):
        try:
            if _running(app["exe"]):
                subprocess.run(["taskkill", "/IM", app["exe"]], capture_output=True, creationflags=_NOWIN)
                FOCUS["closed"].append(app)
        except Exception:
            pass
    return True, f"focus {minutes} min, distractions off"


def focus_end(reason):
    was_mode = FOCUS["mode"]
    FOCUS.update(end=0, minutes=0, mode=False)
    if was_mode:
        try:
            _toasts(bool(FOCUS["toasts"] if FOCUS["toasts"] is not None else 1))
            cfg.pop("_focus_restore_toasts", None)
            save_cfg(cfg)
        except Exception:
            pass
        done = set()
        for app in FOCUS["closed"]:
            if app.get("reopen") and app["reopen"] not in done:
                start(app["reopen"])
                done.add(app["reopen"])
        FOCUS["closed"] = []
    print(f"{time.strftime('%H:%M:%S')}  focus            {reason}")


def focus_loop():
    # a crash or restart mid-session must not leave notifications off for good
    if "_focus_restore_toasts" in cfg:
        try:
            _toasts(bool(cfg.pop("_focus_restore_toasts")))
            save_cfg(cfg)
            print("  focus: notifications restored after a restart")
        except Exception:
            pass
    while True:
        if FOCUS["end"] and time.time() >= FOCUS["end"]:
            focus_end("finished")
            if WINDOWS:
                try:
                    import winsound
                    for _ in range(3):
                        winsound.PlaySound("SystemAsterisk", winsound.SND_ALIAS)
                except Exception:
                    pass
        time.sleep(1)


# ---------------------------------------------------------------- Claude Code permission bridge
class Claude:
    lock = threading.Lock()
    pending = {}   # id -> {"req": {...}, "event": Event, "decision": None}
    last_phone = 0.0


def claude_request(req, wait):
    """Called by the hook. Blocks until the phone answers, or returns None so Claude shows its own prompt."""
    if time.time() - Claude.last_phone > 15:
        return None  # no phone has been looking at the deck recently
    rid = secrets.token_hex(6)
    item = {"req": dict(req, id=rid, at=int(time.time() * 1000)), "event": threading.Event(), "decision": None}
    with Claude.lock:
        Claude.pending[rid] = item
    print(f"{time.strftime('%H:%M:%S')}  claude asks       {req.get('tool')}: {req.get('summary', '')[:60]}")
    item["event"].wait(wait)
    with Claude.lock:
        Claude.pending.pop(rid, None)
    return item["decision"]


def claude_answer(rid, decision):
    with Claude.lock:
        item = Claude.pending.get(rid)
    if not item:
        return False, "That request is no longer waiting"
    item["decision"] = decision
    item["event"].set()
    return True, decision


def claude_pending():
    with Claude.lock:
        items = sorted((i["req"] for i in Claude.pending.values()), key=lambda r: r["at"])
    return items[0] if items else None


# ---------------------------------------------------------------- TickTick (official Open API, one-time OAuth)
TT = {"tasks": [], "err": None, "state": secrets.token_hex(8)}
TT_EV = threading.Event()
TT_AUTH = "https://ticktick.com/oauth/authorize"
TT_TOKEN = "https://ticktick.com/oauth/token"
TT_API = "https://api.ticktick.com/open/v1"
TT_REDIRECT = "http://localhost:{port}/ticktick/callback"


def tt_call(method, path, body=None):
    import urllib.request
    req = urllib.request.Request(TT_API + path, method=method,
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Authorization": "Bearer " + cfg["ticktick_token"],
                                          "Content-Type": "application/json"})
    raw = urllib.request.urlopen(req, timeout=15).read()
    return json.loads(raw) if raw.strip() else None


def tt_parse_due(s):
    import datetime as dt
    try:
        return dt.datetime.strptime(s, "%Y-%m-%dT%H:%M:%S.%f%z")
    except Exception:
        return None


def tasks_loop():
    if not WINDOWS and not cfg.get("ticktick_token"):
        TT["tasks"] = [{"id": "t1", "project": "p1", "title": "Send the monthly invoice", "due": int((time.time() + 3 * 3600) * 1000),
                        "allday": False, "overdue": False, "list": "Work"}]
        return
    import datetime as dt
    while True:
        if cfg.get("ticktick_token") and cfg.get("show_tasks", False):
            try:
                projects = [{"id": "inbox", "name": "Inbox"}] + (tt_call("GET", "/project") or [])
                now = dt.datetime.now(dt.timezone.utc)
                end_today = dt.datetime.now().astimezone().replace(hour=23, minute=59, second=59)
                found = []
                for p in projects:
                    data = tt_call("GET", f"/project/{p['id']}/data") or {}
                    for t in data.get("tasks", []):
                        if t.get("status", 0) != 0 or not t.get("dueDate"):
                            continue
                        due = tt_parse_due(t["dueDate"])
                        if not due or due > end_today:
                            continue
                        allday = bool(t.get("isAllDay"))
                        overdue = (due.date() < dt.date.today()) if allday else due < now
                        found.append({"id": t["id"], "project": t.get("projectId", p["id"]), "title": t.get("title", ""),
                                      "due": int(due.timestamp() * 1000), "allday": allday, "overdue": overdue,
                                      "list": p.get("name", "")})
                found.sort(key=lambda x: (not x["overdue"], x["due"]))
                TT["tasks"], TT["err"] = found[:8], None
            except Exception as e:
                TT["err"] = "TickTick: " + str(e)[:100]
        TT_EV.wait(180)
        TT_EV.clear()


def task_done(pid, tid):
    if not cfg.get("ticktick_token"):
        TT["tasks"] = [t for t in TT["tasks"] if t["id"] != tid]
        return True, "done (demo)"
    try:
        tt_call("POST", f"/project/{pid}/task/{tid}/complete")
        TT["tasks"] = [t for t in TT["tasks"] if t["id"] != tid]
        TT_EV.set()
        return True, "task done"
    except Exception as e:
        return False, "TickTick: " + str(e)[:80]


def tt_connect_url():
    from urllib.parse import urlencode
    return TT_AUTH + "?" + urlencode({"client_id": cfg.get("ticktick_client_id", ""), "scope": "tasks:read tasks:write",
                                      "state": TT["state"], "response_type": "code",
                                      "redirect_uri": TT_REDIRECT.format(port=cfg.get("port", 8765))})


def tt_exchange(code):
    import base64
    import urllib.request
    from urllib.parse import urlencode
    auth = base64.b64encode(f"{cfg['ticktick_client_id']}:{cfg['ticktick_client_secret']}".encode()).decode()
    body = urlencode({"code": code, "grant_type": "authorization_code", "scope": "tasks:read tasks:write",
                      "redirect_uri": TT_REDIRECT.format(port=cfg.get("port", 8765))}).encode()
    req = urllib.request.Request(TT_TOKEN, data=body, method="POST",
                                 headers={"Authorization": "Basic " + auth, "Content-Type": "application/x-www-form-urlencoded"})
    tok = json.loads(urllib.request.urlopen(req, timeout=15).read())["access_token"]
    cfg["ticktick_token"] = tok
    save_cfg(cfg)


# ---------------------------------------------------------------- camera privacy switch and away mode
CAM_KEYS = [r"Software\Microsoft\Windows\CurrentVersion\CapabilityAccessManager\ConsentStore\webcam",
            r"Software\Microsoft\Windows\CurrentVersion\CapabilityAccessManager\ConsentStore\webcam\NonPackaged"]
_CAM_DEMO = {"on": True}


def camera_on():
    """Windows' own per-user camera permission (Settings > Privacy > Camera). No admin needed."""
    if not WINDOWS:
        return _CAM_DEMO["on"]
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, CAM_KEYS[1]) as k:
            return winreg.QueryValueEx(k, "Value")[0] != "Deny"
    except OSError:
        return True


def set_camera(on):
    if not WINDOWS:
        _CAM_DEMO["on"] = on
        return True
    import winreg
    for path in CAM_KEYS:
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, path, 0, winreg.KEY_SET_VALUE) as k:
            winreg.SetValueEx(k, "Value", 0, winreg.REG_SZ, "Allow" if on else "Deny")
    return True


AWAY = {"on": False, "vol": None, "playing": False, "mic": None}


def set_mic(muted):
    if not AUDIO:
        if WINDOWS:
            press("win", "alt", "k")
        return
    _audio(lambda: microphone().SetMute(1 if muted else 0, None))


def go_away():
    st = audio_state()
    AWAY.update(on=True, vol=st.get("vol"), mic=st.get("mic_muted"),
                playing=bool(NOW["info"] and NOW["info"].get("playing")))
    if AWAY["playing"]:
        press("play")
    try:
        set_mic(True)
    except Exception:
        pass
    time.sleep(0.3)
    if WINDOWS:
        user32.LockWorkStation()
    return True, "away"


def come_back():
    if AWAY["mic"] is not None:
        try:
            set_mic(AWAY["mic"])
        except Exception:
            pass
    if AWAY["vol"] is not None:
        set_volume(AWAY["vol"])
    if AWAY["playing"]:
        press("play")
    AWAY.update(on=False, vol=None, playing=False, mic=None)
    return True, "back"


# ---------------------------------------------------------------- eco: screens off, best power efficiency, no lock
# Windows power-mode overlays (the Settings > Power mode slider). User-level, no admin, undone on the way back.
ECO_OVERLAY = "961cc777-2547-4f9d-8174-7d86181b8a7a"  # best power efficiency
ECO = {"on": False, "since": 0, "overlay": None, "playing": False, "mic": None}
ECO_LOCK = threading.Lock()


def _guid(s):
    import uuid
    return (ctypes.c_byte * 16).from_buffer_copy(uuid.UUID(s).bytes_le)


def get_overlay():
    g = (ctypes.c_byte * 16)()
    if ctypes.windll.powrprof.PowerGetEffectiveOverlayScheme(ctypes.byref(g)) != 0:
        return None
    import uuid
    return str(uuid.UUID(bytes_le=bytes(bytearray(x & 0xFF for x in g))))


def set_overlay(s):
    return ctypes.windll.powrprof.PowerSetActiveOverlayScheme(ctypes.byref(_guid(s))) == 0


class LASTINPUTINFO(ctypes.Structure if WINDOWS else object):
    _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)] if WINDOWS else []


def last_input_tick():
    li = LASTINPUTINFO()
    li.cbSize = ctypes.sizeof(LASTINPUTINFO)
    user32.GetLastInputInfo(ctypes.byref(li))
    return li.dwTime


def screens_off():
    # 0xFFFF broadcast, WM_SYSCOMMAND, SC_MONITORPOWER, 2 = off. Post, never Send: a hung window would block us.
    user32.PostMessageW(0xFFFF, 0x0112, 0xF170, 2)


def screens_on():
    user32.mouse_event(0x0001, 0, 1, 0, 0)  # 1px nudge wakes the displays
    user32.mouse_event(0x0001, 0, -1, 0, 0)


def eco_on():
    if not WINDOWS:
        ECO["on"] = True
        return True, "eco on"
    with ECO_LOCK:
        st = audio_state()
        ECO.update(playing=bool(NOW["info"] and NOW["info"].get("playing")), mic=st.get("mic_muted"))
        if ECO["playing"]:
            press("play")
        try:
            set_mic(True)
        except Exception:
            pass
        try:
            prev = get_overlay()
            ECO["overlay"] = prev if (prev and set_overlay(ECO_OVERLAY)) else None
        except Exception:
            ECO["overlay"] = None
        ECO["on"] = True

    def later():
        time.sleep(1.5)  # let the phone get its answer and our own key presses settle
        if not ECO["on"]:
            return
        screens_off()
        time.sleep(1.0)
        ECO["since"] = last_input_tick()
        screens_off()  # some monitors ignore the first one while waking from the previous input

    threading.Thread(target=later, daemon=True).start()
    return True, "screens off"


def eco_off(wake=True):
    if not WINDOWS:
        ECO["on"] = False
        return True, "eco off"
    with ECO_LOCK:
        if not ECO["on"]:
            return True, "already back"
        ECO["on"] = False
        ECO["since"] = 0
        if ECO["overlay"]:
            try:
                set_overlay(ECO["overlay"])
            except Exception:
                pass
        if ECO["mic"] is not None:
            try:
                set_mic(ECO["mic"])
            except Exception:
                pass
        if ECO["playing"]:
            press("play")
        ECO.update(overlay=None, playing=False, mic=None)
    if wake:
        screens_on()
    return True, "back to normal"


def eco_watch():
    """Any key or mouse move on the laptop ends Eco by itself: Windows wakes the screens, we restore the rest."""
    while True:
        time.sleep(2)
        try:
            if ECO["on"] and ECO["since"] and last_input_tick() != ECO["since"]:
                eco_off(wake=False)
        except Exception:
            pass


if WINDOWS:
    threading.Thread(target=eco_watch, daemon=True).start()


def tile_states():
    cam = camera_on()
    c = claude_pending()
    return {"eco": {"on": ECO["on"], "sub": "Touch any key to wake" if ECO["on"] else None},
            "approve": {"on": bool(c), "title": ("Allow " + c["tool"]) if c else None,
                        "sub": c["summary"][:60] if c else None},
"dictate": {"on": DICTATING["on"], "sub": "Listening… tap to stop" if DICTATING["on"] else None},
            "away": {"on": AWAY["on"], "sub": "Tap when back" if AWAY["on"] else None, "title": "Back" if AWAY["on"] else None},
            "camera": {"on": not cam, "sub": "Off · apps blocked" if not cam else "On"},
            "focus30": {"on": bool(FOCUS["end"]), "focus_end": int(FOCUS["end"] * 1000) if FOCUS["end"] else None,
                        "sub": None}}


# ---------------------------------------------------------------- stats
class Stats:
    cpu = 0.0
    _last = None

    @classmethod
    def sample_cpu(cls):
        if not WINDOWS:
            return
        idle, kern, usr = (ctypes.c_ulonglong() for _ in range(3))
        kernel32.GetSystemTimes(ctypes.byref(idle), ctypes.byref(kern), ctypes.byref(usr))
        now = (idle.value, kern.value, usr.value)
        if cls._last:
            di, dk, du = (now[i] - cls._last[i] for i in range(3))
            cls.cpu = round(100 * (1 - di / (dk + du)), 0) if (dk + du) else 0.0
        cls._last = now

    @staticmethod
    def battery():
        if not WINDOWS:
            return 87, True

        class PWR(ctypes.Structure):
            _fields_ = [("ac", ctypes.c_ubyte), ("flag", ctypes.c_ubyte), ("pct", ctypes.c_ubyte),
                        ("sys", ctypes.c_ubyte), ("life", wintypes.DWORD), ("full", wintypes.DWORD)]
        p = PWR()
        if kernel32.GetSystemPowerStatus(ctypes.byref(p)) and p.pct != 255:
            return p.pct, p.ac == 1
        return None, None


def cpu_loop():
    while True:
        Stats.sample_cpu()
        time.sleep(2)


# ---------------------------------------------------------------- actions
DICTATING = {"on": False}


def open_whatsapp_chat(name):
    w = cfg["whatsapp"]
    if not focus(w["process_contains"]):
        for t in w["launch"]:
            if start(t):
                break
        time.sleep(w.get("launch_wait", 2.5))
        if not focus(w["process_contains"]):
            return False, "WhatsApp didn't open"
    press(*w["search_keys"])
    time.sleep(0.35)
    press("ctrl", "a")
    type_text(name)
    time.sleep(w.get("result_wait", 0.8))
    for k in w["open_keys"]:
        press(*k.split("+"))
        time.sleep(0.15)
    return True, "opened"


def join_meeting():
    """Volume to 40%, then open the next meeting's link if it starts within 15 minutes or is on now."""
    set_volume(0.40)
    n = MEET.get("next")
    if n and n.get("link") and n["start"] / 1000 - time.time() < 15 * 60:
        start(n["link"])
        return True, "joining " + n["title"]
    return True, "volume 40%, no meeting link due"


def run_action(aid, value=None):
    if aid == "vol:set" and value is not None:
        return set_volume(value), "volume"
    if aid == "vol:mute":
        return toggle_speaker_mute(), "speaker"
    if aid == "mic:toggle":
        return toggle_mic(), "mic"
    if aid in ("media:prev", "media:play", "media:next"):
        press(aid.split(":")[1])
        return True, "media"
    if aid == "bright:set" and value is not None:
        return set_brightness(value), "brightness"
    if aid == "focus:start":
        return focus_mode_start(max(1, min(180, int(value or 25))))
    if aid == "focus:stop":
        focus_end("ended early")
        return True, "focus stopped"
    if aid == "meeting:join":
        return join_meeting()
    if aid.startswith("task:done:"):
        parts = aid.split(":")
        if len(parts) != 4:
            return False, "unknown"
        return task_done(parts[2], parts[3])
    if aid.startswith("claude:"):
        _, kind, rid = (aid.split(":") + ["", ""])[:3]
        if kind not in ("allow", "always", "deny", "pc"):
            return False, "unknown"
        return claude_answer(rid, kind)
    if aid.startswith("approve:"):
        kind = aid.split(":")[1]
        a = cfg["approve"]
        if kind not in ("once", "always", "deny"):
            return False, "unknown"
        if not focus(a["process"]):
            return False, "Claude window not found"
        press(*a[kind])
        return True, "sent"
    a = ACTIONS.get(aid)
    if not a:
        return False, "unknown action"
    t = a["type"]
    if t == "meeting":
        return join_meeting()
    if t == "admin_shell":
        if not WINDOWS:
            print("  [dry] admin powershell")
            return True, "check the laptop"
        # Windows shows its own "allow changes?" prompt on the laptop; nothing here can skip it
        r = ctypes.windll.shell32.ShellExecuteW(None, "runas", "powershell.exe", "-NoExit -Command Set-Location $HOME",
                                                 None, 1)
        return (r > 32), ("approve the prompt on the laptop" if r > 32 else "cancelled on the laptop")
    if t == "focusmode":
        if FOCUS["mode"] or FOCUS["end"]:
            focus_end("ended early")
            return True, "focus ended"
        return focus_mode_start(int(a.get("minutes", 30)))
    if t == "away":
        return come_back() if AWAY["on"] else go_away()
    if t == "eco":
        return eco_off() if ECO["on"] else eco_on()
    if t == "claude_approve":
        c = claude_pending()
        if not c:
            return False, "Nothing waiting from Claude"
        ok, _ = claude_answer(c["id"], "allow")
        return ok, ("Allowed: " + c["tool"]) if ok else "That request is no longer waiting"
    if t == "camera":
        on = not camera_on()
        set_camera(on)
        return True, "camera on" if on else "camera off"
    if t == "focus":
        return run_action("focus:start", a.get("minutes", 25))
    if t == "keys":
        press(*a["keys"])
        if a.get("toggle"):
            DICTATING["on"] = not DICTATING["on"]
        return True, "done"
    if t == "whatsapp":
        return open_whatsapp_chat(a["chat"])
    if t == "open":
        return start(a["target"]), "opened"
    if t == "lock":
        if WINDOWS:
            user32.LockWorkStation()
        return True, "locked"
    return False, "unknown type"


# ---------------------------------------------------------------- real app icons
def icon_file(app_id):
    for name in (app_id, app_id + "-fav"):
        for ext in (".png", ".svg", ".ico"):
            if os.path.isfile(os.path.join(ICON_DIR, name + ext)):
                return "icons/" + name + ext
    return None


def fetch_icons():
    import urllib.request
    os.makedirs(ICON_DIR, exist_ok=True)
    for a in cfg.get("icons", []):
        if icon_file(a["id"]):
            continue
        urls = [(u, a["id"]) for u in a.get("icon_urls", [])]
        if a.get("site"):
            urls.append(("https://t1.gstatic.com/faviconV2?client=SOCIAL&type=FAVICON"
                         "&fallback_opts=TYPE,SIZE,URL&size=256&url=https://" + a["site"], a["id"] + "-fav"))
        for u, name in urls:
            try:
                data = urllib.request.urlopen(urllib.request.Request(u, headers={"User-Agent": "Mozilla/5.0"}),
                                              timeout=8).read()
            except Exception:
                continue
            if len(data) < 600:
                continue
            if data[:4] == b"\x89PNG":
                ext = ".png"
            elif data[:4] == b"\x00\x00\x01\x00":
                ext = ".ico"
            elif b"<svg" in data[:400]:
                ext = ".svg"
            else:
                continue
            with open(os.path.join(ICON_DIR, name + ext), "wb") as f:
                f.write(data)
            print(f"  icon  {a['id']:<10} ok")
            break


# ---------------------------------------------------------------- http
FAILS = {"n": 0, "until": 0.0}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def send(self, code, body, ctype="application/json"):
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def authed(self):
        if time.time() < FAILS["until"]:
            return False
        if secrets.compare_digest(self.headers.get("X-Pin", ""), cfg["pin"]):
            FAILS["n"] = 0
            return True
        FAILS["n"] += 1
        if FAILS["n"] >= 8:
            FAILS["n"], FAILS["until"] = 0, time.time() + 60
        return False

    def serve_file(self, rel):
        path = os.path.normpath(os.path.join(STATIC, rel))
        if not path.startswith(STATIC) or not os.path.isfile(path):
            return self.send(404, {"error": "not found"})
        ext = os.path.splitext(path)[1].lower()
        ctype = {".html": "text/html; charset=utf-8", ".jpg": "image/jpeg", ".png": "image/png",
                 ".ico": "image/x-icon", ".svg": "image/svg+xml",
                 ".json": "application/manifest+json"}.get(ext, "application/octet-stream")
        with open(path, "rb") as f:
            self.send(200, f.read(), ctype)

    def do_GET(self):
        p = unquote(urlparse(self.path).path)
        if p in ("/", "/index.html"):
            return self.serve_file("index.html")
        if p.startswith("/static/"):
            return self.serve_file(p[len("/static/"):])
        if p.startswith("/ticktick/"):
            return self.ticktick(p)
        if p.startswith("/api/") and not self.authed():
            return self.send(401, {"error": "pin"})
        if p == "/api/config":
            return self.send(200, {"actions": public_tiles(cfg["actions"]), "claude_icon": icon_file("claude"),
                                   "presets": public_tiles(PRESETS.values()),
                                   "features": {"audio": AUDIO or not WINDOWS, "brightness": BRIGHT,
                                                "media": MEDIA or not WINDOWS,
                                                "calendar": bool(cfg.get("calendar_ics")) or not WINDOWS,
                                                "tasks": bool(cfg.get("ticktick_token")) or not WINDOWS}})
        if p == "/api/state":
            Claude.last_phone = time.time()
            pct, charging = Stats.battery()
            st = audio_state()
            st.update({"cpu": Stats.cpu, "battery": pct, "charging": charging, "dictating": DICTATING["on"], "tiles": tile_states(),
                       "bright": Bright.value, "now": NOW["info"], "art": NOW["key"] if NOW["art"] else None,
                       "meeting": MEET["next"], "meeting_err": MEET["err"],
                       "focus": {"end": int(FOCUS["end"] * 1000), "minutes": FOCUS["minutes"], "mode": FOCUS["mode"]} if FOCUS["end"] else None,
                       "claude": claude_pending(), "tasks": TT["tasks"], "tasks_err": TT["err"],
                       "server_time": int(time.time() * 1000)})
            return self.send(200, st)
        if p == "/api/art":
            if NOW["art"]:
                kind = "image/png" if NOW["art"][:4] == b"\x89PNG" else "image/jpeg"
                return self.send(200, NOW["art"], kind)
            return self.send(404, {"error": "no art"})
        self.send(404, {"error": "not found"})

    def ticktick(self, p):
        """One-time TickTick connection. Works only from the laptop itself (localhost)."""
        if self.client_address[0] != "127.0.0.1":
            return self.send(403, {"error": "open this on the laptop"})
        page = lambda msg: self.send(200, f"<html><body style='font:18px -apple-system,Segoe UI;background:#0b0b0f;color:#fff;display:grid;place-items:center;height:100vh'><div>{msg}</div></body></html>".encode(), "text/html; charset=utf-8")
        if p == "/ticktick/connect":
            if not cfg.get("ticktick_client_id") or not cfg.get("ticktick_client_secret"):
                return page("Add your TickTick Client ID and Client Secret to config.json first.")
            self.send_response(302)
            self.send_header("Location", tt_connect_url())
            self.end_headers()
            return
        if p == "/ticktick/callback":
            from urllib.parse import parse_qs
            q = parse_qs(urlparse(self.path).query)
            if q.get("state", [""])[0] != TT["state"] or "code" not in q:
                return page("That link expired. Open http://localhost:8765/ticktick/connect again.")
            try:
                tt_exchange(q["code"][0])
                TT_EV.set()
                print(f"{time.strftime('%H:%M:%S')}  ticktick         connected")
                return page("TickTick is connected. You can close this tab.")
            except Exception as e:
                return page("TickTick refused the connection: " + str(e)[:120])
        self.send(404, {"error": "not found"})

    def read_json(self):
        n = int(self.headers.get("Content-Length", "0"))
        return json.loads(self.rfile.read(n) or b"{}")

    def do_POST(self):
        path = urlparse(self.path).path
        if path == "/api/hook":
            return self.hook()
        if path not in ("/api/action", "/api/tiles"):
            return self.send(404, {"error": "not found"})
        if not self.authed():
            return self.send(401, {"error": "pin"})
        try:
            body = self.read_json()
            if path == "/api/tiles":
                ok, msg = save_tiles(body.get("tiles", []))
                print(f"{time.strftime('%H:%M:%S')}  tiles            {msg}")
                return self.send(200 if ok else 400, {"ok": ok, "msg": msg})
            aid = body.get("id", "")
            ok, msg = run_action(aid, body.get("value"))
            if aid not in ("vol:set", "bright:set"):
                print(f"{time.strftime('%H:%M:%S')}  {aid:<16} {msg}")
            self.send(200 if ok else 409, {"ok": ok, "msg": msg})
        except Exception as e:
            print("error:", e)
            self.send(500, {"ok": False, "msg": str(e)})

    def hook(self):
        """Only Claude Code's hook on this same PC may call this, with the secret from config.json."""
        if self.client_address[0] != "127.0.0.1" or \
                not secrets.compare_digest(self.headers.get("X-Deck-Hook", ""), cfg["hook_secret"]):
            return self.send(403, {"error": "forbidden"})
        try:
            req = self.read_json()
            decision = claude_request(req, float(cfg.get("claude_wait_seconds", 45)))
            self.send(200, {"decision": decision})
        except Exception as e:
            self.send(200, {"decision": None, "error": str(e)})


def public_tiles(items):
    out = []
    for a in items:
        t = {k: a.get(k) for k in ("id", "title", "sub", "glyph", "avatar", "icon", "toggle", "type", "chat", "url")}
        if t.get("icon"):
            t["icon"] = icon_file(t["icon"])
        out.append(t)
    return out


def save_tiles(tiles):
    """The phone sends the tile list. Each is a preset id or a WhatsApp chat; nothing else is accepted."""
    if not isinstance(tiles, list) or not 1 <= len(tiles) <= 9:
        return False, "between 1 and 9 tiles"
    new, seen = [], set()
    for t in tiles:
        if t.get("type") == "whatsapp":
            chat = str(t.get("chat", "")).strip()[:80]
            title = str(t.get("title", chat)).strip()[:24] or chat
            if not chat:
                return False, "a chat needs a name"
            tid = t.get("id") if str(t.get("id", "")).startswith("wa_") or t.get("id") in ACTIONS else None
            tid = tid or "wa_" + secrets.token_hex(3)
            new.append({"id": tid, "title": title, "sub": "WhatsApp", "avatar": title[:1].upper(),
                        "icon": "whatsapp", "type": "whatsapp", "chat": chat})
        elif t.get("id") in PRESETS:
            new.append(dict(PRESETS[t["id"]]))
        elif t.get("id") in ACTIONS and ACTIONS[t["id"]]["type"] != "whatsapp":
            new.append(dict(ACTIONS[t["id"]]))
        else:
            return False, "unknown tile"
        if new[-1]["id"] in seen:
            return False, "duplicate tile"
        seen.add(new[-1]["id"])
    cfg["actions"] = new
    ACTIONS.clear()
    ACTIONS.update({a["id"]: a for a in new})
    save_cfg(cfg)
    return True, f"saved {len(new)}"


def local_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except OSError:
        return "127.0.0.1"


if __name__ == "__main__":
    import sys
    if sys.stdout is None:  # started hidden with pyw: write the log to deck.log instead
        sys.stdout = sys.stderr = open(os.path.join(BASE, "deck.log"), "a", encoding="utf-8", buffering=1)
    for fn in (cpu_loop, fetch_icons, bright_loop, media_loop, calendar_loop, focus_loop, tasks_loop):
        threading.Thread(target=fn, daemon=True).start()
    port = int(cfg.get("port", 8765))
    class DeckServer(ThreadingHTTPServer):
        allow_reuse_address = False  # on Windows, reuse lets a second copy share the port and steal connections
        daemon_threads = True

        def server_bind(self):
            if WINDOWS:
                self.socket.setsockopt(socket.SOL_SOCKET, getattr(socket, "SO_EXCLUSIVEADDRUSE", -5), 1)
            super().server_bind()
    try:
        srv = DeckServer(("0.0.0.0", port), Handler)
    except OSError as e:
        print(f"  Port {port} is already in use, so another copy of the deck is running ({e}). Exiting.")
        raise SystemExit(1)
    print("\n  Control Deck is running")
    print(f"  On the iPhone, open:  http://{local_ip()}:{port}")
    print(f"  PIN:                  {cfg['pin']}")
    missing = [n for n, ok in (("pycaw", AUDIO), ("screen-brightness-control", BRIGHT),
                               ("winrt-Windows.Media.Control", MEDIA)) if WINDOWS and not ok]
    if missing:
        print("  Optional, not installed:  " + ", ".join(missing))
    print("  Stop with Ctrl+C\n")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
