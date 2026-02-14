import requests
import json
import re
import logging
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from config import OPENROUTER_API_KEY, OPENROUTER_URL, OPENROUTER_MODEL

logger = logging.getLogger(__name__)

# Create a session with retries to avoid repeating code and improve resilience
_session = requests.Session()
retries = Retry(total=3, backoff_factor=0.5, status_forcelist=(429, 500, 502, 503, 504))
adapter = HTTPAdapter(max_retries=retries)
_session.mount("https://", adapter)
_session.mount("http://", adapter)

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

def _extract_json_from_text(text: str):
    """Try to extract the first JSON object from a string.
    Falls back to returning None if no JSON is found.
    """
    if not text:
        return None
    # Quick attempt: if text looks like JSON already
    text = text.strip()
    if (text.startswith("{") and text.endswith("}")) or (text.startswith("[") and text.endswith("]")):
        try:
            return json.loads(text)
        except Exception:
            pass

    # Search for the first {...} block
    match = re.search(r"(\{.*\})", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1))
        except Exception:
            pass
    return None


def generate_question(skill: str, difficulty: str, qtype: str, options: int = 4):
    prompt_text = PROMPTS.get(qtype, PROMPTS.get("mcq")).format(skill=skill, difficulty=difficulty, options=options)

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
        "max_tokens": 250
    }

    logger.info(f"Generating {qtype} question for skill={skill}, difficulty={difficulty}")
    logger.debug(f"Using model: {OPENROUTER_MODEL}, URL: {OPENROUTER_URL}")
    
    try:
        resp = _session.post(OPENROUTER_URL, json=payload, headers=headers, timeout=60)
        logger.debug(f"API response status: {resp.status_code}")
        
        if resp.status_code != 200:
            logger.error(f"API returned status {resp.status_code}: {resp.text}")
            return None
        
        data = resp.json()
    except requests.exceptions.RequestException as e:
        logger.error(f"Network/Request error calling OpenRouter API: {str(e)}")
        return None
    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse API response as JSON: {str(e)}")
        return None
    except Exception as e:
        logger.error(f"Unexpected error in generate_question: {str(e)}")
        return None

    content = None
    try:
        content = data["choices"][0]["message"]["content"]
        logger.debug(f"Received content from API (first 100 chars): {content[:100]}")
    except Exception as e:
        logger.error(f"Failed to extract content from API response: {str(e)}")
        logger.debug(f"API response structure: {json.dumps(data)}")
        return None

    parsed = _extract_json_from_text(content)
    if parsed is None:
        logger.warning(f"Could not extract JSON from API response for {qtype}")
        logger.debug(f"Raw content: {content}")
        return None

    # NORMALIZE OUTPUT HERE
    if qtype == "mcq":
        result = {
            "question": parsed.get("prompt") or parsed.get("question") or parsed.get("prompt_text"),
            "options": parsed.get("options", []),
            "correct_answer": parsed.get("answer") or parsed.get("correct_answer")
        }
        logger.debug(f"MCQ generated: {result['question'][:80]}")
        return result

    if qtype == "coding":
        result = {
            "question": parsed.get("prompt") or parsed.get("question"),
            "input_spec": parsed.get("input_spec"),
            "output_spec": parsed.get("output_spec"),
            "examples": parsed.get("examples", [])
        }
        logger.debug(f"Coding question generated: {result['question'][:80]}")
        return result

    if qtype == "audio":
        result = {
            "prompt_text": parsed.get("prompt_text") or parsed.get("prompt"),
            "expected_keywords": parsed.get("expected_keywords", []),
            "rubric": parsed.get("rubric")
        }
        logger.debug(f"Audio question generated: {result['prompt_text'][:80]}")
        return result

    if qtype == "video":
        result = {
            "prompt_text": parsed.get("prompt_text") or parsed.get("prompt"),
            "rubric": parsed.get("rubric"),
            "suggested_time_seconds": parsed.get("suggested_time_seconds", 60)
        }
        logger.debug(f"Video question generated: {result['prompt_text'][:80]}")
        return result

    logger.debug(f"Parsed result: {parsed}")
    return parsed


def evaluate_answer(question_type: str, question_text: str, correct_answer: str, candidate_answer: str):
    """
    Evaluate MCQ, Coding, Audio, or Video question answers using LLM (OpenRouter).
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

    elif question_type == "audio":
        eval_prompt = (
            f"You are an evaluator for audio interview answers.\n"
            f"Question: {question_text}\n"
            f"Ideal Answer Description: {correct_answer}\n"
            f"Candidate's Transcribed Answer: {candidate_answer}\n"
            f"Evaluate the candidate's answer for relevance, completeness, and clarity. "
            f"Score from 0 (poor) to 5 (excellent). "
            f"Return JSON ONLY with keys: score (0-5), feedback (short explanation)."
        )

    elif question_type == "video":
        eval_prompt = (
            f"You are an evaluator for video interview answers.\n"
            f"Question: {question_text}\n"
            f"Ideal Answer Description: {correct_answer}\n"
            f"Candidate's Transcribed Answer: {candidate_answer}\n"
            f"Evaluate the candidate's answer for relevance, communication, and depth. "
            f"Score from 0 (poor) to 5 (excellent). "
            f"Return JSON ONLY with keys: score (0-5), feedback (short explanation)."
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

    resp = _session.post(OPENROUTER_URL, json=payload, headers=headers, timeout=60)
    resp.raise_for_status()
    data = resp.json()

    try:
        content = data["choices"][0]["message"]["content"]
        parsed = _extract_json_from_text(content)
        return parsed if parsed is not None else {"raw": content}
    except Exception:
        return {"raw": content}