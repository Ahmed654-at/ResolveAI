"""Writes every step of every case to the agent_logs table, and prints a case back.

Show a case:  python -m agent.logger              (latest case)
              python -m agent.logger <case_id>    (a specific case)
"""

import json
import sys
from datetime import datetime

from agent import tools


def log(case_id, step, event, tool_name=None, input=None, output=None):
    """Write one log row. Never raises: a lost log line is better than a lost case."""
    try:
        with tools.connect() as conn:
            conn.execute(
                "INSERT INTO agent_logs (case_id, step, event, tool_name, input, output, timestamp)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                (case_id, step, event, tool_name, _text(input), _text(output),
                 datetime.now().isoformat(timespec="milliseconds")),
            )
    except Exception as e:
        print(f"WARNING: could not write log for {case_id}: {e}", file=sys.stderr)


def _text(value):
    """Store dicts as JSON so they can be read back; leave strings as they are."""
    if value is None or isinstance(value, str):
        return value
    return json.dumps(value)


def get_case(case_id=None):
    """All log rows for a case, oldest first. With no case_id, the most recent case."""
    with tools.connect() as conn:
        if case_id is None:
            row = conn.execute("SELECT case_id FROM agent_logs ORDER BY id DESC LIMIT 1").fetchone()
            if row is None:
                return []
            case_id = row["case_id"]
        rows = conn.execute(
            "SELECT * FROM agent_logs WHERE case_id = ? ORDER BY id", (case_id,)
        ).fetchall()
    return [dict(r) for r in rows]


def list_cases(limit=50):
    """Recent finished cases, newest first: one row per case from its 'decision' log."""
    with tools.connect() as conn:
        rows = conn.execute(
            """
            SELECT d.case_id, d.timestamp, d.output, s.input AS message
            FROM agent_logs d
            LEFT JOIN agent_logs s ON s.case_id = d.case_id AND s.event = 'start'
            WHERE d.event = 'decision'
            ORDER BY d.id DESC LIMIT ?
            """,
            (limit,),
        ).fetchall()
    cases = []
    for r in rows:
        decision = json.loads(r["output"])
        cases.append({
            "case_id": r["case_id"],
            "time": r["timestamp"][:19].replace("T", " "),
            "outcome": decision["outcome"],
            "reason": decision.get("reason"),
            "steps": decision.get("steps"),
            "seconds": decision.get("seconds"),
            "message": r["message"],
        })
    return cases


def print_case(case_id=None):
    rows = get_case(case_id)
    if not rows:
        print("No logs found." + (f" (case {case_id})" if case_id else " Run a case first."))
        return
    print(f"Case {rows[0]['case_id']}\n")
    for r in rows:
        time = r["timestamp"][11:19]
        label = f"tool: {r['tool_name']}" if r["event"] == "tool" else r["event"]
        print(f"[step {r['step']:>2}] {time}  {label}")
        if r["input"]:
            print(f"    in:  {r['input']}")
        if r["output"]:
            print(f"    out: {r['output']}")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    print_case(sys.argv[1] if len(sys.argv) > 1 else None)
