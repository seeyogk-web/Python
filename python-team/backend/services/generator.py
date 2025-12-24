import re
import uuid
from services.llm_client import generate_question

def generate_questions(payload):
    """
    Orchestrates question generation for each skill and type.
    """
    all_questions = []
    skills = payload.get("skills", [])
    global_settings = payload.get("global_settings", {"mcq_options": 4})

    # Keep track of normalized question texts to avoid duplicates
    seen_texts = set()

    def _normalize_text(val):
        if not val:
            return ""
        s = str(val).lower().strip()
        s = re.sub(r"[^\w\s]", "", s)
        s = re.sub(r"\s+", " ", s)
        return s

    for skill in skills:
        name = skill.get("name")
        difficulty = skill.get("difficulty", "medium")
        counts = skill.get("counts", {})

        for qtype, num in counts.items():
            for _ in range(num):
                attempts = 0
                q_data = None
                while attempts < 5:
                    attempts += 1
                    try:
                        q_data = generate_question(
                            skill=name,
                            difficulty=difficulty,
                            qtype=qtype,
                            options=global_settings.get("mcq_options", 4),
                        )
                    except Exception:
                        # LLM failed; prepare a sensible fallback
                        if qtype == "audio":
                            q_data = {
                                "prompt_text": f"Describe a situation where you used {name} effectively.",
                                "type": "audio"
                            }
                        elif qtype == "video":
                            q_data = {
                                "prompt_text": f"Record a short video explaining a {name}-related challenge you solved.",
                                "type": "video"
                            }
                        else:
                            q_data = {"prompt": f"Generate a question for {name}", "type": qtype}

                    # Extract a representative text for deduplication
                    # Include MCQ options and coding specs to avoid near-duplicates
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
                    if not norm or norm in seen_texts:
                        # try again to get a different question
                        q_data = None
                        continue
                    # unique question found
                    seen_texts.add(norm)
                    break

                # If still None (couldn't get unique), force a small variant to ensure uniqueness
                if q_data is None:
                    base = rep or f"{qtype} question about {name}"
                    suffix = f" (variant {uuid.uuid4().hex[:6]})"
                    if isinstance(rep, str) and rep:
                        if "prompt_text" in (q_data or {}):
                            q_data = {"prompt_text": base + suffix, "type": qtype}
                        else:
                            q_data = {"prompt": base + suffix, "type": qtype}
                    else:
                        q_data = {"prompt": base + suffix, "type": qtype}

                all_questions.append({
                    "question_id": str(uuid.uuid4()),
                    "skill": name,
                    "type": qtype,
                    "difficulty": difficulty,
                    "content": q_data
                })

    return all_questions
