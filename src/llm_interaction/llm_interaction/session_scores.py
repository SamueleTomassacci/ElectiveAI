"""Session score calculation, persistence, and history queries."""


import json
import os
import threading
from pathlib import Path

ACTIVITY_TO_TASK = {
    "storytelling": "Take the book and give it to the child",
    "mood_check_teddy": "Take the teddy bear and give it to the child",
    "computer_emotion_recognition": "Take the computer and give it to the child",
}

TASK_KEYWORD_TO_ACTIVITY = [
    (["book", "read","story"],"storytelling", "Take the book and give it to the child"),
    (
        ["teddy","teddy bear", "plush", "peluche","mood","feelings","how they feel"],
        "mood_check_teddy",
        "Take the teddy bear and give it to the child",
    ),
    (
        ["computer", "laptop","emotion recognition","recognize emotions", "emotion photos"],
        "computer_emotion_recognition",
        "Take the computer and give it to the child",
    ),
]

EMOTION_VALENCE = {
    "happy": 100.0,
    "surprise": 80.0,
    "neutral": 60.0,
    "unknown": 50.0,
    "disgust": 25.0,
    "sad": 25.0,
    "angry": 15.0,
    "fear": 15.0,
}

ATTENTION_PENALTY_PER_ALERT_SEC = 5.0

file_lock = threading.Lock()


def compute_session_scores(
    *,
    attention_alert_count,
    emotion_samples,
    child_turns,
    expected_turns,
    duration_sec,
):
    attention = attention_score(attention_alert_count,duration_sec)
    emotion= emotion_score(emotion_samples)
    participation = participation_score(child_turns, expected_turns)
    overall= round(0.4 * attention + 0.3 * emotion + 0.3 * participation, 1)
    return {
        "attention": attention,
        "emotion": emotion,
        "participation": participation,
        "overall": overall,
    }


def attention_score(alert_count,duration_sec):
    if duration_sec <= 0:
        return 50.0
    penalty = alert_count * ATTENTION_PENALTY_PER_ALERT_SEC
    score =100.0 * (1.0 - penalty / max(1.0,duration_sec))
    return round(max(0.0,min(100.0,score)),1)


def emotion_score(samples):
    if not samples:
        return 50.0
    total=0.0
    count= 0
    for _,emotion in samples:
        em = emotion.lower().strip()
        if em in ("no_face", "unavailable"):
            continue
        total += EMOTION_VALENCE.get(em, 50.0)
        count += 1
    if count == 0:
        return 50.0
    return round(total/count, 1)


def participation_score(child_turns,expected_turns):
    if expected_turns <= 0:
        return 100.0 if child_turns > 0 else 50.0
    ratio = min(1.0, child_turns / max(1,expected_turns))
    return round(100.0 * ratio,1)


def load_score_history(path):
    if not path or not os.path.exists(path):
        return {"children": {}}
    with file_lock:

        try:
            with open(path,"r",encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError):
            return {"children": {}}

    if not isinstance(data, dict) or "children" not in data:
        return {"children": {}}
    return data


def save_session_score(
    path,
    child_name,
    session_data,
):
    key = child_name.strip().lower()
    if not key:
        return
    with file_lock:

        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:

                    data = json.load(f)
            except (json.JSONDecodeError,OSError):
                data = {"children": {}}
        else:
            data = {"children": {}}

        if not isinstance(data, dict) or "children" not in data:
            data = {"children": {}}

        children= data["children"]
        if key not in children:
            children[key] = {"sessions": []}
        children[key]["sessions"].append(session_data)

        Path(path).parent.mkdir(parents=True,exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data,f, indent=2, ensure_ascii=False)


def best_activity_for_child(
    path,
    child_name,
):
    history = load_score_history(path)
    key = child_name.strip().lower()
    child_data = history.get("children",{}).get(key)
    if not child_data:
        return None

    sessions= child_data.get("sessions",[])
    if not sessions:
        return None

    activity_scores = {}
    activity_tasks = {}
    for session in sessions:
        atype = session.get("activity_type", "")
        overall = session.get("scores", {}).get("overall")
        if atype and overall is not None:
            activity_scores.setdefault(atype,[]).append(float(overall))
            task = session.get("task_description", "")
            if task:
                activity_tasks[atype] = task

    if not activity_scores:
        return None

    best_type = max(activity_scores, key=lambda k: sum(activity_scores[k]) / len(activity_scores[k]))
    avg_score = sum(activity_scores[best_type]) / len(activity_scores[best_type])
    task = activity_tasks.get(best_type,ACTIVITY_TO_TASK.get(best_type, ""))

    return {
        "activity_type": best_type,
        "task_description": task,
        "average_score": round(avg_score,1),
        "session_count": len(activity_scores[best_type]),
    }


def infer_activity_and_task(text):
    lower = text.lower()
    for keywords, activity_type, task in TASK_KEYWORD_TO_ACTIVITY:
        if any(kw in lower for kw in keywords):
            return activity_type,task
    return None
