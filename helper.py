"""Runs the parts of the deck that use native Windows components, in a separate process.
If one of them crashes, only this helper dies and the deck keeps running.

  helper.py media            -> prints {"info": {...} | null, "art": base64 | null}
  helper.py bright-get       -> prints {"value": 0-100 | null}
  helper.py bright-set 60    -> sets every display that supports it
"""
import base64
import json
import sys


def media():
    import asyncio
    from winrt.windows.media.control import GlobalSystemMediaTransportControlsSessionManager as M
    from winrt.windows.storage.streams import Buffer, InputStreamOptions

    async def run():
        mgr = await M.request_async()
        s = mgr.get_current_session()
        if not s:
            return {"info": None, "art": None}
        p = await s.try_get_media_properties_async()
        status = int(s.get_playback_info().playback_status)
        app = (s.source_app_user_model_id or "").split("!")[0].split(".")[-1]
        info = {"title": p.title or "", "artist": p.artist or "", "album": p.album_title or "",
                "playing": status == 4, "app": app}
        art = None
        if p.thumbnail and "--no-art" not in sys.argv:
            try:
                st = await p.thumbnail.open_read_async()
                buf = Buffer(st.size)
                await st.read_async(buf, st.size, InputStreamOptions.READ_AHEAD)
                art = base64.b64encode(bytes(memoryview(buf))).decode()
            except Exception:
                art = None
        return {"info": info, "art": art}
    return asyncio.run(run())


def bright_get():
    import screen_brightness_control as sbc
    vals = sbc.get_brightness()
    return {"value": int(vals[-1]) if vals else None}


def bright_set(v):
    import screen_brightness_control as sbc
    sbc.set_brightness(int(v))
    return {"ok": True}


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    try:
        out = media() if cmd == "media" else bright_get() if cmd == "bright-get" else \
            bright_set(sys.argv[2]) if cmd == "bright-set" else {"error": "unknown"}
    except Exception as e:
        out = {"error": str(e)[:200]}
    sys.stdout.write(json.dumps(out))
