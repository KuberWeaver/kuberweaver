#!/usr/bin/env python3
"""Deterministic next-task selector for the enterprise-services autopilot."""
import json, sys, datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
STATE = HERE / "STATE.json"

def main():
    data = json.loads(STATE.read_text())
    if (HERE / "STOP").exists():
        print(json.dumps({"action": "stop", "reason": "STOP marker"})); return
    deadline = datetime.datetime.fromisoformat(data["deadline"])
    now = datetime.datetime.now(deadline.tzinfo)
    if now >= deadline:
        print(json.dumps({"action": "stop", "reason": "deadline passed"})); return
    for task in data["tasks"]:
        if task["status"] in ("pending", "partial"):
            if task.get("attempts", 0) >= 3:
                task["status"] = "blocked"
                task.setdefault("note", "3 attempts exhausted")
                STATE.write_text(json.dumps(data, indent=2))
                continue
            print(json.dumps({"action": "work", "rules": str(HERE / "RULES.md"),
                              "task": task}, indent=2)); return
    print(json.dumps({"action": "stop", "reason": "no work remaining"}))

if __name__ == "__main__":
    main()
