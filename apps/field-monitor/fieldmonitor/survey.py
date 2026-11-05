"""Opening survey: how each farm is used to farming, so reminders fit the farmer.

The questions are data (QUESTIONS): reword, reorder, add, or remove them here and the page follows.
Answers are stored per farm, append-only (every submission is kept; the newest wins), and turned
into reminder preferences by `preferences()`:

- usual planting start/finish dates per crop (used where the state calendar is still TODO),
- what tells the farmer to start (soil temperature or a calendar/insurance date),
- the soil temperature they wait for,
- whether they take stand counts,
- the hour they want morning reminders (for phone push).

Practices like tillage, irrigation, and who scouts are saved now for the scouting and spray
reminders that come later.
"""

from __future__ import annotations

import json
import re
from typing import Any, Optional

from .accounts import Accounts, now

QUESTIONS: list[dict[str, Any]] = [
    {"id": "crops", "type": "multi", "required": True, "text": "Which crops do you grow?",
     "options": [["corn", "Corn"], ["soybeans", "Soybeans"], ["wheat", "Wheat"], ["other", "Something else"]]},
    {"id": "start_trigger", "type": "single", "text": "What tells you it's time to start planting?",
     "options": [["soil_temp", "Soil temperature"], ["calendar", "A date on the calendar"],
                 ["insurance_date", "My crop insurance earliest planting date"], ["conditions", "When the ground is fit (my judgment)"]]},
    {"id": "corn_start", "type": "monthday", "if": ["crops", "corn"], "text": "When do you usually start planting corn?"},
    {"id": "corn_finish", "type": "monthday", "if": ["crops", "corn"], "text": "And when do you usually want corn finished?"},
    {"id": "corn_soil_f", "type": "single", "if": ["crops", "corn"], "text": "What 2-inch soil temperature do you wait for before planting corn?",
     "options": [["50", "50°F (extension guidance)"], ["55", "55°F"], ["none", "I don't go by soil temperature"]]},
    {"id": "soy_start", "type": "monthday", "if": ["crops", "soybeans"], "text": "When do you usually start planting soybeans?"},
    {"id": "soy_finish", "type": "monthday", "if": ["crops", "soybeans"], "text": "And when do you usually want soybeans finished?"},
    {"id": "soy_soil_f", "type": "single", "if": ["crops", "soybeans"], "text": "What 2-inch soil temperature do you wait for before planting soybeans?",
     "options": [["50", "50°F"], ["55", "55°F"], ["60", "60°F"], ["none", "I don't go by soil temperature"]]},
    {"id": "wheat_start", "type": "monthday", "if": ["crops", "wheat"], "text": "When do you usually start planting wheat in the fall?"},
    {"id": "wheat_finish", "type": "monthday", "if": ["crops", "wheat"], "text": "And when do you usually want wheat planted by?"},
    {"id": "order", "type": "single", "text": "Which do you usually plant first?",
     "options": [["corn_first", "Corn first"], ["soybeans_first", "Soybeans first"], ["same_time", "Both at the same time"], ["one_crop", "I only plant one"]]},
    {"id": "stand_counts", "type": "single", "text": "Do you take stand counts after emergence?",
     "options": [["always", "Every field"], ["sometimes", "Some fields or when something looks off"], ["never", "Not usually"]]},
    {"id": "tillage", "type": "single", "text": "How do you mostly prepare ground?",
     "options": [["no_till", "No-till"], ["strip_till", "Strip-till"], ["minimum", "Minimum till"], ["conventional", "Conventional tillage"]]},
    {"id": "irrigation", "type": "single", "text": "How much of your ground is irrigated?",
     "options": [["all", "All of it"], ["some", "Some"], ["none", "None (dryland)"]]},
    {"id": "scouting_who", "type": "single", "text": "Who usually scouts your fields?",
     "options": [["me", "I do"], ["employee", "Someone on my crew"], ["agronomist", "My agronomist or co-op"],
                 ["consultant", "A crop consultant"], ["nobody", "Nobody regularly"]]},
    {"id": "scouting_frequency", "type": "single", "text": "How often are fields scouted in season?",
     "options": [["weekly", "Weekly"], ["every_2_weeks", "Every 2 weeks"], ["as_needed", "When something comes up"]]},
    {"id": "reminder_hour", "type": "single", "text": "When do you want your morning reminders?",
     "options": [["5", "5 AM"], ["6", "6 AM"], ["7", "7 AM"], ["8", "8 AM"], ["off", "No phone reminders"]]},
]
_BY_ID = {q["id"]: q for q in QUESTIONS}
_MONTHDAY = re.compile(r"^(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])$")

SCHEMA = """
CREATE TABLE IF NOT EXISTS farm_surveys (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  farm_id TEXT NOT NULL REFERENCES farms(id),
  answers TEXT NOT NULL,
  answered_by TEXT NOT NULL REFERENCES users(id),
  answered_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS farm_surveys_by_farm ON farm_surveys(farm_id, id);
"""


def _applies(q: dict[str, Any], answers: dict[str, Any]) -> bool:
    cond = q.get("if")
    return not cond or cond[1] in (answers.get(cond[0]) or [])


def validate(raw: dict[str, Any]) -> dict[str, Any]:
    """Clean answers; unknown ids and questions that don't apply are dropped. Raises ValueError."""
    out: dict[str, Any] = {}
    for qid, val in raw.items():
        q = _BY_ID.get(qid)
        if q is None or val in (None, "", []):
            continue
        allowed = {o[0] for o in q.get("options", [])}
        if q["type"] == "multi":
            vals = [val] if isinstance(val, str) else list(val)
            bad = [v for v in vals if v not in allowed]
            if bad:
                raise ValueError(f"{q['text']} Unknown choice: {', '.join(map(str, bad))}")
            out[qid] = list(dict.fromkeys(vals))
        elif q["type"] == "single":
            if str(val) not in allowed:
                raise ValueError(f"{q['text']} Unknown choice: {val}")
            out[qid] = str(val)
        elif q["type"] == "monthday":
            s = str(val)[-5:]  # accept YYYY-MM-DD from a date picker
            if not _MONTHDAY.match(s):
                raise ValueError(f"{q['text']} Use a date like 04-20.")
            out[qid] = s
    for q in QUESTIONS:
        if q.get("required") and q["id"] not in out:
            raise ValueError(f"Please answer: {q['text']}")
    return {k: v for k, v in out.items() if _applies(_BY_ID[k], out)}


class Surveys:
    def __init__(self, accounts: Accounts) -> None:
        self.accounts = accounts
        with accounts.db() as c:
            c.executescript(SCHEMA)

    def save(self, farm_id: str, user_id: str, answers: dict[str, Any]) -> dict[str, Any]:
        clean = validate(answers)
        with self.accounts.db() as c:
            c.execute("INSERT INTO farm_surveys (farm_id, answers, answered_by, answered_at) VALUES (?,?,?,?)",
                      (farm_id, json.dumps(clean), user_id, now()))
        return self.latest(farm_id)  # type: ignore[return-value]

    def latest(self, farm_id: Optional[str]) -> Optional[dict[str, Any]]:
        if not farm_id:
            return None
        with self.accounts.db() as c:
            row = c.execute("SELECT * FROM farm_surveys WHERE farm_id=? ORDER BY id DESC LIMIT 1", (farm_id,)).fetchone()
        if not row:
            return None
        return {"farm_id": farm_id, "answers": json.loads(row["answers"]), "answered_by": row["answered_by"],
                "answered_at": row["answered_at"]}


def preferences(answers: Optional[dict[str, Any]]) -> dict[str, Any]:
    """Reminder preferences from survey answers (empty dict = use the defaults)."""
    if not answers:
        return {}
    a = answers
    prefs: dict[str, Any] = {"trigger": a.get("start_trigger"), "stand_counts": a.get("stand_counts"),
                             "reminder_hour": a.get("reminder_hour")}
    for crop, key in (("corn", "corn"), ("soybean", "soy"), ("wheat", "wheat")):
        p: dict[str, Any] = {}
        if a.get(f"{key}_start"):
            p["start"] = a[f"{key}_start"]
        if a.get(f"{key}_finish"):
            p["finish"] = a[f"{key}_finish"]
        soil = a.get(f"{key}_soil_f")
        if soil and soil != "none":
            p["soil_f"] = float(soil)
        elif soil == "none":
            p["no_soil_rule"] = True
        if p:
            prefs[crop] = p
    return prefs
