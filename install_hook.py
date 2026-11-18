"""Adds (or with --remove, takes out) the Control Deck permission hook in your Claude Code user settings.

Backs up settings.json first, next to it, with the date and time in the name. Touches only the
"hooks" > "PermissionRequest" entry that points at deck_hook.py; every other setting stays as it is.
"""
import datetime
import json
import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
HOOK = os.path.join(HERE, "deck_hook.py")
SETTINGS = os.path.join(os.path.expanduser("~"), ".claude", "settings.json")


def is_ours(entry):
    return any("deck_hook.py" in " ".join([h.get("command", "")] + h.get("args", []))
               for h in entry.get("hooks", []))


def main():
    remove = "--remove" in sys.argv
    os.makedirs(os.path.dirname(SETTINGS), exist_ok=True)
    data = {}
    if os.path.exists(SETTINGS):
        stamp = datetime.datetime.now().strftime("%Y-%m-%d_%H%M%S")
        backup = os.path.join(os.path.dirname(SETTINGS), f"settings.backup-{stamp}.json")
        n = 2
        while os.path.exists(backup):  # never overwrite an earlier backup
            backup = os.path.join(os.path.dirname(SETTINGS), f"settings.backup-{stamp}-{n}.json")
            n += 1
        shutil.copy2(SETTINGS, backup)
        print("Backup:", backup)
        with open(SETTINGS, encoding="utf-8") as f:
            data = json.load(f)

    hooks = data.setdefault("hooks", {})
    entries = [e for e in hooks.get("PermissionRequest", []) if not is_ours(e)]
    if not remove:
        exe = sys.executable
        if exe.lower().endswith("pythonw.exe"):
            exe = exe[:-len("pythonw.exe")] + "python.exe"
        with open(os.path.join(HERE, "config.json"), encoding="utf-8") as f:
            wait = float(json.load(f).get("claude_wait_seconds", 45))
        # one command string: Claude Code has no "args" field and drops it
        entries.append({"matcher": "*", "hooks": [{"type": "command", "command": f'"{exe}" "{HOOK}"',
                                                   "timeout": int(wait + 15)}]})
    if entries:
        hooks["PermissionRequest"] = entries
    else:
        hooks.pop("PermissionRequest", None)
        if not hooks:
            data.pop("hooks")

    with open(SETTINGS, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    print("Removed the Control Deck hook." if remove else "Added the Control Deck hook.", "Restart Claude to apply.")


if __name__ == "__main__":
    main()
