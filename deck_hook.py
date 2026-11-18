"""Claude Code PermissionRequest hook -> Control Deck.

Claude Code runs this when it is about to ask you for permission. It passes the request to the deck so you
can answer on the iPhone. If the deck is not running, no phone is watching, you tap "Decide on PC", or anything
goes wrong, this prints nothing and Claude shows its normal prompt on the PC. It never approves on its own.
"""
import json
import os
import sys
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))


def summarise(tool, ti):
    if not isinstance(ti, dict):
        return str(ti)[:300]
    for k in ("command", "file_path", "path", "url", "pattern", "query", "description"):
        if ti.get(k):
            return str(ti[k])[:300]
    return json.dumps(ti, ensure_ascii=False)[:300]


def main():
    data = json.loads(sys.stdin.read() or "{}")
    with open(os.path.join(HERE, "config.json"), encoding="utf-8") as f:
        cfg = json.load(f)
    tool = data.get("tool_name", "")
    ti = data.get("tool_input", {})
    req = {"tool": tool, "summary": summarise(tool, ti), "cwd": data.get("cwd", ""),
           "detail": (ti.get("description") if isinstance(ti, dict) else "") or ""}
    wait = float(cfg.get("claude_wait_seconds", 45))
    r = urllib.request.Request(f"http://127.0.0.1:{cfg.get('port', 8765)}/api/hook",
                               data=json.dumps(req).encode(), method="POST",
                               headers={"Content-Type": "application/json", "X-Deck-Hook": cfg["hook_secret"]})
    decision = json.loads(urllib.request.urlopen(r, timeout=wait + 5).read()).get("decision")

    if decision == "allow":
        d = {"behavior": "allow"}
    elif decision == "always":
        d = {"behavior": "allow"}
        if data.get("permission_suggestions"):
            d["updatedPermissions"] = data["permission_suggestions"]
    elif decision == "deny":
        d = {"behavior": "deny", "message": "Denied from Control Deck"}
    else:
        return  # decide on the PC: print nothing
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "PermissionRequest", "decision": d}}))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass  # any problem: Claude falls back to its normal prompt
    sys.exit(0)
