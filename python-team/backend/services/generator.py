import re
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading
from services.llm_client import generate_question


def generate_questions(payload):
    """Generate questions in parallel while avoiding duplicates.

    - Uses a thread pool for parallel LLM calls (bounded by total questions).
    - Keeps a thread-safe `seen_texts` set to deduplicate generated content.
    """
    skills = payload.get("skills", [])
    global_settings = payload.get("global_settings", {"mcq_options": 4})

    # Build a flat list of generation tasks
    tasks = []  # items are (skill_name, difficulty, qtype, options)
    for skill in skills:
        name = skill.get("name")
        if not name:
            continue
        difficulty = skill.get("difficulty", "medium")
        counts = skill.get("counts", {}) or {}
        for qtype, num in counts.items():
            try:
                num = int(num)
            except Exception:
                num = 0
            for _ in range(max(0, num)):
                tasks.append((name, difficulty, qtype, global_settings.get("mcq_options", 4)))

    if not tasks:
        return []

    # Thread-safe deduplication set
    seen_texts = set()
    seen_lock = threading.Lock()

    def _normalize_text(val):
        if not val:
            return ""
        s = str(val).lower().strip()
        s = re.sub(r"[^\w\s]", "", s)
        s = re.sub(r"\s+", " ", s)
        return s

    def _create_one(name, difficulty, qtype, options):
        # Try multiple times to get a unique, valid question
        attempts = 0
        last_rep = None
        while attempts < 5:
            attempts += 1
            try:
                q_data = generate_question(skill=name, difficulty=difficulty, qtype=qtype, options=options)
            except Exception:
                q_data = None

            if q_data is None:
                # fallback content
                if qtype == "audio":
                    q_data = {"prompt_text": f"Describe a situation where you used {name} effectively.", "type": "audio"}
                elif qtype == "video":
                    q_data = {"prompt_text": f"Record a short video explaining a {name}-related challenge you solved.", "type": "video"}
                else:
                    q_data = {"prompt": f"Generate a question for {name}", "type": qtype}

            # Build representative text
            rep = ""
            if isinstance(q_data, dict):
                if qtype == "mcq":
                    q_text = q_data.get("question") or q_data.get("prompt") or q_data.get("prompt_text") or ""
                    opts = " ".join(q_data.get("options", []) or [])
                    rep = f"{q_text} {opts}".strip()
                elif qtype == "coding":
                    q_text = q_data.get("question") or q_data.get("prompt") or ""
                    input_spec = q_data.get("input_spec", "") or ""
                    output_spec = q_data.get("output_spec", "") or ""
                    rep = f"{q_text} {input_spec} {output_spec}".strip()
                else:
                    rep = q_data.get("question") or q_data.get("prompt_text") or q_data.get("prompt") or str(q_data)
            else:
                rep = str(q_data)

            norm = _normalize_text(rep)
            last_rep = rep
            if not norm:
                continue

            with seen_lock:
                if norm in seen_texts:
                    # duplicate, try again
                    continue
                seen_texts.add(norm)

            # unique question
            return {
                "question_id": str(uuid.uuid4()),
                "skill": name,
                "type": qtype,
                "difficulty": difficulty,
                "content": q_data
            }

        # If here, couldn't get unique within attempts -> force variant
        base = last_rep or f"{qtype} question about {name}"
        suffix = f" (variant {uuid.uuid4().hex[:6]})"
        if isinstance(base, str):
            content = {"prompt": base + suffix, "type": qtype}
        else:
            content = {"prompt": f"{qtype} question about {name}{suffix}", "type": qtype}

        # ensure uniqueness recorded
        with seen_lock:
            seen_texts.add(_normalize_text(base + suffix))

        return {
            "question_id": str(uuid.uuid4()),
            "skill": name,
            "type": qtype,
            "difficulty": difficulty,
            "content": content
        }

    # Run tasks in a bounded thread pool
    max_workers = min(8, len(tasks))
    results = []
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        future_to_task = {ex.submit(_create_one, *t): t for t in tasks}
        for fut in as_completed(future_to_task):
            try:
                res = fut.result()
                if res:
                    results.append(res)
            except Exception:
                # on unexpected failure, skip this question
                continue

    return results
