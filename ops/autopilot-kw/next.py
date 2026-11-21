#!/usr/bin/env python3
"""Deterministic next-step selector for the kw CLI unattended build.

Prints the first pending/partial task as JSON (with the rules path), or action=stop.
Enforces the absolute deadline. No inference, no side effects.
"""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
STATE = HERE / "STATE.json"


def main() -> None:
    data = json.loads(STATE.read_text())
    deadline = datetime.fromisoformat(data["deadline"])
    now = datetime.now(timezone.utc)
    if now >= deadline:
        print(json.dumps({"action": "stop", "reason": "deadline reached",
                          "rules": str(HERE / "RULES.md")}))
        return
    for task in data["tasks"]:
        if task["status"] in ("pending", "partial"):
            if task["attempts"] >= 3:
                task["status"] = "blocked"
                task["note"] = (task.get("note", "") + " [3 attempts exhausted]").strip()
                STATE.write_text(json.dumps(data, indent=2))
                continue
            print(json.dumps({"action": "work", "rules": str(HERE / "RULES.md"),
                              "state": str(STATE), "verify": data["verify"],
                              "branch": data["branch"], "task": task}, indent=2))
            return
    print(json.dumps({"action": "stop", "reason": "no pending tasks",
                      "rules": str(HERE / "RULES.md")}))


if __name__ == "__main__":
    main()
