import requests
import json
from config import OPENROUTER_API_KEY, OPENROUTER_URL, OPENROUTER_MODEL

PROMPTS = {
    "mcq": (
        "Generate ONE multiple-choice question for skill '{skill}' "
        "with difficulty '{difficulty}'. Provide {options} answer options "
        "labeled A, B, C, D. Return JSON ONLY with keys: prompt, options (list), answer (single letter)."
    ),
    "coding": (
        "Generate ONE coding question for skill '{skill}' "
        "with difficulty '{difficulty}'. Return JSON ONLY with keys: prompt, input_spec, output_spec, examples (list)."
    ),
    "audio": (
        "Generate ONE interview question for skill '{skill}' "
        "with difficulty '{difficulty}'. The question should be short and clear. "
        "Return JSON ONLY with keys: prompt_text, expected_keywords (list), rubric (short)."
    ),
    "video": (
        "Generate ONE interview question for skill '{skill}' "
        "with difficulty '{difficulty}'. The question should be short and clear. "
        "Return JSON ONLY with keys: prompt_text, rubric (short), suggested_time_seconds."
    ),
}

def generate_question(skill: str, difficulty: str, qtype: str, options: int = 4):
    prompt_text = PROMPTS[qtype].format(skill=skill, difficulty=difficulty, options=options)

    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json"
    }

    payload = {
        "model": OPENROUTER_MODEL,
        "messages": [
            {"role": "system", "content": "You are a helpful interview question generator."},
            {"role": "user", "content": prompt_text}
        ],
        "temperature": 0.3,
        "max_tokens": 600
    }

    resp = requests.post(OPENROUTER_URL, json=payload, headers=headers, timeout=60)
    resp.raise_for_status()
    data = resp.json()

    try:
        content = data["choices"][0]["message"]["content"]
        parsed = json.loads(content)
    except Exception:
        return {"question": "Bad LLM response", "options": [], "correct_answer": None}

    # NORMALIZE OUTPUT HERE
    if qtype == "mcq":
        return {
            "question": parsed.get("prompt"),
            "options": parsed.get("options", []),
            "correct_answer": parsed.get("answer")   # A/B/C/D
        }

    if qtype == "coding":
        return {
            "question": parsed.get("prompt"),
            "input_spec": parsed.get("input_spec"),
            "output_spec": parsed.get("output_spec"),
            "examples": parsed.get("examples", [])
        }

    if qtype == "audio":
        return {
            "question": parsed.get("prompt_text"),
            "expected_keywords": parsed.get("expected_keywords", []),
            "rubric": parsed.get("rubric")
        }

    if qtype == "video":
        return {
            "question": parsed.get("prompt_text"),
            "rubric": parsed.get("rubric"),
            "suggested_time_seconds": parsed.get("suggested_time_seconds", 60)
        }

    return parsed


def evaluate_answer(question_type: str, question_text: str, correct_answer: str, candidate_answer: str):
    """
    Evaluate MCQ or Coding question answers using LLM (OpenRouter).
    Returns a structured JSON with evaluation result.
    """

    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json"
    }

    if question_type == "mcq":
        eval_prompt = (
            f"You are an evaluator for multiple-choice questions.\n"
            f"Question: {question_text}\n"
            f"Correct Answer: {correct_answer}\n"
            f"Candidate Answer: {candidate_answer}\n"
            f"Evaluate if the candidate's answer is correct.\n"
            f"Return JSON ONLY with keys: is_correct (true/false), score (0 or 1), feedback (short sentence)."
        )

    elif question_type == "coding":
        eval_prompt = (
            f"You are an evaluator for coding questions.\n"
            f"Question: {question_text}\n"
            f"Expected Solution Description: {correct_answer}\n"
            f"Candidate Code:\n{candidate_answer}\n"
            f"Evaluate correctness and efficiency. "
            f"Return JSON ONLY with keys: score (0-10), feedback (short explanation)."
        )

    else:
        raise ValueError("Unsupported question_type for evaluation")

    payload = {
        "model": OPENROUTER_MODEL,
        "messages": [
            {"role": "system", "content": "You are a strict and fair evaluator for technical questions."},
            {"role": "user", "content": eval_prompt}
        ],
        "temperature": 0.2,
        "max_tokens": 400
    }

    resp = requests.post(OPENROUTER_URL, json=payload, headers=headers, timeout=60)
    resp.raise_for_status()
    data = resp.json()

    try:
        content = data["choices"][0]["message"]["content"]
        return json.loads(content)
    except Exception:
        return {"raw": content}