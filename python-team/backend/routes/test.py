from flask import Blueprint, request, jsonify
from config import get_db_connection
from services.llm_client import evaluate_answer
import os
import psycopg2
import secrets
import json
import uuid
from datetime import datetime
from werkzeug.utils import secure_filename

UPLOAD_DIR = os.getenv("UPLOAD_DIR", "recordings")
os.makedirs(UPLOAD_DIR, exist_ok=True)

test_bp = Blueprint("test", __name__)

# ==============================================
# Start Test
# ==============================================
@test_bp.route("/test/start/<question_set_id>", methods=["GET"])
def start_test(question_set_id):
    conn = None
    cursor = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        cursor.execute("""
            SELECT id, content
            FROM questions
            WHERE question_set_id = %s
        """, (uuid.UUID(question_set_id),))
        rows = cursor.fetchall()

        questions_list = []

        for qid, raw in rows:
            # qid may be UUID object
            qid_str = str(qid)
            raw_json = json.loads(raw) if isinstance(raw, str) else raw
            inner = raw_json.get("content", {})

            questions_list.append({
                "id": qid_str,
                "question_id": qid_str,
                "type": raw_json.get("type"),
                "skill": raw_json.get("skill"),
                "difficulty": raw_json.get("difficulty"),
                "time_limit": raw_json.get("time_limit"),
                "positive_marking": raw_json.get("positive_marking"),
                "negative_marking": raw_json.get("negative_marking"),
                "question": inner.get("question"),
                "options": inner.get("options"),
                "correct_answer": inner.get("correct_answer"),
                "prompt_text": inner.get("prompt_text"),
                "media_url": inner.get("media_url"),
                "rubric": inner.get("rubric"),
                "suggested_time_seconds": inner.get("suggested_time_seconds"),
            })

        return jsonify({
            "question_set_id": question_set_id,
            "questions": questions_list
        }), 200

    except Exception as e:
        print("🔥 start_test error:", e)
        return jsonify({"error": str(e)}), 500

    finally:
        if cursor: cursor.close()
        if conn: conn.close()

# ==============================================
# Save Violations
# ==============================================
@test_bp.route("/test/save_violations", methods=["POST"])
def save_violations():
    data = request.get_json() or {}

    candidate_id = data.get("candidate_id")
    question_set_id = data.get("question_set_id")
    tab_switches = data.get("tab_switches", 0)
    inactivities = data.get("inactivities", 0)
    face_not_visible = data.get("face_not_visible", 0)

    if not candidate_id or not question_set_id:
        return jsonify({"error": "candidate_id and question_set_id required"}), 400

    conn = None
    cur = None
    try:
        conn = get_db_connection()
        cur = conn.cursor()

        cur.execute("""
            INSERT INTO test_attempts (
                candidate_id, question_set_id,
                tab_switches, inactivities, face_not_visible
            )
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (candidate_id, question_set_id)
            DO UPDATE SET
                tab_switches = EXCLUDED.tab_switches,
                inactivities = EXCLUDED.inactivities,
                face_not_visible = EXCLUDED.face_not_visible;
        """, (
            uuid.UUID(candidate_id),
            uuid.UUID(question_set_id),
            tab_switches,
            inactivities,
            face_not_visible
        ))

        conn.commit()
        return jsonify({"message": "Violations updated"}), 200

    except Exception as e:
        print("🔥 ERROR saving violations:", e)
        return jsonify({"error": str(e)}), 500

    finally:
        if cur: cur.close()
        if conn: conn.close()

# ==============================================
# Upload Audio
# ==============================================
@test_bp.route("/upload_audio", methods=["POST"])
def upload_audio():
    conn = None
    cur = None
    try:
        if "audio" not in request.files:
            return jsonify({"error": "audio file required"}), 400

        audio_file = request.files["audio"]
        if audio_file.filename == "":
            return jsonify({"error": "empty filename"}), 400

        candidate_id = request.form.get("candidate_id")
        question_set_id = request.form.get("question_set_id")
        qa_raw = request.form.get("qa_data") or "[]"

        try:
            qa_data = json.loads(qa_raw)
        except Exception:
            qa_data = []

        ext = os.path.splitext(audio_file.filename)[1] or ".webm"
        ts = datetime.utcnow().strftime("%Y%m%d%H%M%S")
        safe = secure_filename(f"{candidate_id}_{ts}{ext}")
        save_path = os.path.join(UPLOAD_DIR, safe)
        audio_file.save(save_path)
        audio_url = f"/{UPLOAD_DIR}/{safe}"

        conn = get_db_connection()
        cur = conn.cursor()

        cur.execute("""
            INSERT INTO test_attempts (
                candidate_id, question_set_id, audio_url, qa_data
            ) VALUES (%s, %s, %s, %s)
            ON CONFLICT (candidate_id, question_set_id)
            DO UPDATE SET
                audio_url = COALESCE(EXCLUDED.audio_url, test_attempts.audio_url),
                qa_data = COALESCE(test_attempts.qa_data, '[]'::jsonb) || COALESCE(EXCLUDED.qa_data, '[]'::jsonb);
        """, (
            uuid.UUID(candidate_id),
            uuid.UUID(question_set_id),
            audio_url,
            json.dumps(qa_data)
        ))

        conn.commit()

        return jsonify({"status": "success", "audio_url": audio_url}), 200

    except Exception as e:
        print("🔥 upload_audio error:", e)
        return jsonify({"error": str(e)}), 500

    finally:
        if cur: cur.close()
        if conn: conn.close()

# ==============================================
# Upload Video
# ==============================================
@test_bp.route("/upload_video", methods=["POST"])
def upload_video():
    conn = None
    cur = None
    try:
        if "file" not in request.files:
            return jsonify({"error": "video file required"}), 400

        video_file = request.files["file"]
        if video_file.filename == "":
            return jsonify({"error": "empty filename"}), 400

        candidate_id = request.form.get("candidate_id")
        question_set_id = request.form.get("question_set_id")
        qa_raw = request.form.get("qa_data") or "[]"

        try:
            qa_data = json.loads(qa_raw)
        except Exception:
            qa_data = []

        safe = secure_filename(video_file.filename)
        ts = datetime.utcnow().strftime("%Y%m%d%H%M%S")
        final_name = f"{candidate_id}_{ts}_{safe}"
        save_path = os.path.join(UPLOAD_DIR, final_name)
        video_file.save(save_path)
        video_url = f"/{UPLOAD_DIR}/{final_name}"

        conn = get_db_connection()
        cur = conn.cursor()

        cur.execute("""
            INSERT INTO test_attempts (
                candidate_id, question_set_id, video_url, qa_data
            ) VALUES (%s, %s, %s, %s)
            ON CONFLICT (candidate_id, question_set_id)
            DO UPDATE SET
                video_url = COALESCE(EXCLUDED.video_url, test_attempts.video_url),
                qa_data = COALESCE(test_attempts.qa_data, '[]'::jsonb) || COALESCE(EXCLUDED.qa_data, '[]'::jsonb);
        """, (
            uuid.UUID(candidate_id),
            uuid.UUID(question_set_id),
            video_url,
            json.dumps(qa_data)
        ))

        conn.commit()

        return jsonify({"status": "success", "video_url": video_url}), 200

    except Exception as e:
        print("🔥 upload_video error:", e)
        return jsonify({"error": str(e)}), 500

    finally:
        if cur: cur.close()
        if conn: conn.close()

# ==============================================
# Submit Section
# ==============================================
@test_bp.route("/test/submit_section", methods=["POST"])
def submit_section():
    data = request.get_json() or {}

    # prefer path parameter if supplied, otherwise body
    question_set_id = question_set_id or data.get("question_set_id")
    section_name = data.get("section_name")
    responses = data.get("responses", [])
    candidate_id = data.get("candidate_id")

    if not candidate_id or not question_set_id:
        return jsonify({"error": "candidate_id and question_set_id required"}), 400

    conn = None
    cursor = None
    try:
        # Open DB connection early to read question metadata for this question_set
        conn = get_db_connection()
        cursor = conn.cursor()

        cursor.execute("""
            SELECT id, content
            FROM questions
            WHERE question_set_id = %s
        """, (question_set_id,))
        rows = cursor.fetchall()

        # Map question_id -> content dict (stored under content key)
        question_meta = {}
        for qid, raw in rows:
            qid_str = str(qid)
            try:
                raw_json = json.loads(raw) if isinstance(raw, str) else raw
            except Exception:
                raw_json = raw
            if isinstance(raw_json, dict):
                content = raw_json.get("content") or {}
                # merge top-level positive/negative markings into content for easier access
                if raw_json.get("positive_marking") is not None:
                    try:
                        content["positive_marking"] = raw_json.get("positive_marking")
                    except Exception:
                        pass
                if raw_json.get("negative_marking") is not None:
                    try:
                        content["negative_marking"] = raw_json.get("negative_marking")
                    except Exception:
                        pass
            else:
                content = {}
            question_meta[qid_str] = content

        results_out = []

        # log raw incoming responses for debugging
        try:
            print("submit_section: raw_responses=", json.dumps(responses))
        except Exception:
            print("submit_section: raw_responses (non-serializable)=", responses)

        for r in responses:
            qid = r.get("question_id")
            qtype = r.get("question_type")
            qtext = r.get("question_text")
            correct = r.get("correct_answer")
            answer = r.get("candidate_answer")
            # accept alternative field names if frontend uses them
            if not answer:
                answer = r.get("answer") or r.get("response") or r.get("candidate_response") or r.get("transcript")

            if qtype in ["mcq", "coding"]:
                try:
                    evaluation = evaluate_answer(
                        question_type=qtype,
                        question_text=qtext,
                        correct_answer=correct,
                        candidate_answer=answer,
                    )
                except Exception:
                    evaluation = {"score": 0, "feedback": "Evaluation failed", "is_correct": False}

            elif qtype in ["audio", "video"]:
                # Evaluate based on generated parameters (expected keywords, suggested time)
                try:
                    # Normalize candidate answer and extract transcript/duration if provided
                    transcript = ""
                    duration = None
                    if isinstance(answer, dict):
                        transcript = answer.get("transcript") or answer.get("text") or ""
                        duration = answer.get("duration_seconds") or answer.get("duration")
                    else:
                        # may be JSON string
                        try:
                            parsed = json.loads(answer) if isinstance(answer, str) else None
                            if isinstance(parsed, dict):
                                transcript = parsed.get("transcript") or parsed.get("text") or ""
                                duration = parsed.get("duration_seconds") or parsed.get("duration")
                            else:
                                transcript = str(answer or "")
                        except Exception:
                            transcript = str(answer or "")

                    def _clean(s):
                        return (s or "").lower()

                    transcript_clean = _clean(transcript)

                    # Parse expected keywords / params from `correct`
                    expected_keywords = []
                    suggested_time = None
                    if isinstance(correct, dict):
                        expected_keywords = correct.get("expected_keywords") or correct.get("keywords") or []
                        suggested_time = correct.get("suggested_time_seconds") or correct.get("suggested_time")
                    else:
                        # attempt to parse JSON string or comma-separated keywords
                        if isinstance(correct, str):
                            try:
                                parsed_c = json.loads(correct)
                                if isinstance(parsed_c, dict):
                                    expected_keywords = parsed_c.get("expected_keywords") or parsed_c.get("keywords") or []
                                    suggested_time = parsed_c.get("suggested_time_seconds") or parsed_c.get("suggested_time")
                                elif isinstance(parsed_c, list):
                                    expected_keywords = parsed_c
                            except Exception:
                                # fallback: comma separated
                                    expected_keywords = [k.strip() for k in correct.split(",") if k.strip()]

                        # If expected_keywords is missing or placeholder (e.g. "N/A"), try to pull from question_meta
                        try:
                            meta = question_meta.get(str(qid)) or {}
                            if (not expected_keywords) or (expected_keywords == ["N/A"]) or (isinstance(correct, str) and correct.strip().upper() == "N/A"):
                                meta_kws = meta.get("expected_keywords") or meta.get("keywords") or []
                                if meta_kws:
                                    expected_keywords = meta_kws
                            if not suggested_time:
                                # try several keys used when questions are stored
                                suggested_time = meta.get("suggested_time_seconds") or meta.get("suggested_time") or meta.get("suggested_time_seconds")
                        except Exception:
                            pass

                    # Score keywords presence
                    matches = 0
                    total = max(1, len(expected_keywords))
                    for kw in expected_keywords:
                        kw_clean = (kw or "").lower()
                        if not kw_clean:
                            continue
                        if kw_clean in transcript_clean:
                            matches += 1

                    keyword_score = matches / total if total > 0 else 0

                    # Time score for video: only compute if both duration and suggested_time present
                    time_score = None
                    st = suggested_time if suggested_time is not None else (correct.get("suggested_time_seconds") if isinstance(correct, dict) else None)

                    if duration is not None and st is not None:
                        try:
                            dur = float(duration)
                            stf = float(st)
                            # consider acceptable window 0.5x - 1.5x of suggested
                            if 0.5 * stf <= dur <= 1.5 * stf:
                                time_score = 1.0
                            else:
                                # penalize proportionally
                                time_score = max(0.0, 1 - abs(dur - stf) / stf)
                        except Exception:
                            time_score = None

                    # Combine scores: audio mostly keywords; video uses keyword + time (20% weight)
                    if qtype == "audio":
                        combined = keyword_score
                    else:
                        # If no time info available, rely only on keyword score
                        if time_score is None:
                            combined = 0.8 * keyword_score
                        else:
                            combined = 0.8 * keyword_score + 0.2 * time_score

                    # For audio/video return score in range [0.0, 1.0]
                    score = round(float(combined), 3)
                    is_correct = combined >= 0.6
                    missing = [k for k in expected_keywords if k.lower() not in transcript_clean]
                    feedback = ""
                    if matches == total:
                        feedback = "All expected keywords present"
                    else:
                        feedback = f"Found {matches}/{total} keywords. Missing: {', '.join(missing)}"

                    evaluation = {"score": score, "feedback": feedback, "is_correct": is_correct}
                except Exception:
                    evaluation = {"score": 0, "feedback": "Audio/video evaluation failed", "is_correct": False}

            else:
                evaluation = {"score": None, "feedback": "Not evaluated", "is_correct": False}
            # Use question `positive_marking` as the scoring scale
            try:
                meta = question_meta.get(str(qid)) or {}
                print("$$$$$$$$$$$$$$$$$$$$$$4",question_meta)
                print("-------------------",meta)
                pos_mark = meta.get("positive_marking")
                pos_mark = float(pos_mark) if pos_mark is not None else None
            except Exception:
                pos_mark = None

            raw_score = evaluation.get("score")

            # If positive_marking is provided, convert evaluator raw score to that scale.
            # - MCQ: if `is_correct` True -> full `pos_mark`, else 0 (all-or-nothing)
            # - Coding: evaluator returns 0-10 -> scale to pos_mark via (raw/10)*pos_mark
            # - Audio/Video: evaluator returns 0-1 -> scale via raw*pos_mark
            try:
                if pos_mark is not None and raw_score is not None:
                    if qtype == "mcq":
                        is_corr = evaluation.get("is_correct")
                        evaluation["score"] = float(pos_mark) if is_corr else 0.0
                    elif qtype == "coding":
                        raw_f = float(raw_score)
                        evaluation["score"] = round((raw_f / 10.0) * float(pos_mark), 3)
                    else:
                        # audio/video or other types that return 0-1
                        raw_f = float(raw_score)
                        evaluation["score"] = round(raw_f * float(pos_mark), 3)
                else:
                    # No positive_marking provided: keep evaluator score as-is
                    if raw_score is not None:
                        # normalize coding to 0-10 representation if evaluator returned numeric
                        if qtype == "coding":
                            try:
                                evaluation["score"] = round(float(raw_score), 3)
                            except Exception:
                                pass
                        else:
                            try:
                                evaluation["score"] = round(float(raw_score), 3)
                            except Exception:
                                pass
            except Exception:
                pass

            # Debug/log: print marks information before persisting
            try:
                print(
                    f"MARKS: candidate_id={candidate_id} question_id={qid} type={qtype} "
                    f"raw_score={raw_score!r} positive_marking={pos_mark!r} final_score={evaluation.get('score')!r} "
                    f"is_correct={evaluation.get('is_correct')!r}"
                )
            except Exception:
                pass

            results_out.append({
                "question_id": qid,
                "candidate_answer": answer,
                "correct_answer": correct,
                "section_name": section_name,
                "score": evaluation.get("score"),
                "is_correct": evaluation.get("is_correct"),
                "feedback": evaluation.get("feedback")
            })

        # Log evaluation output for debugging
        try:
            print(f"submit_section: question_set_id={question_set_id} candidate_id={candidate_id}")
            print("submit_section: question_meta_keys=", list(question_meta.keys()))
            try:
                print("submit_section: evaluations=", json.dumps(results_out))
            except Exception:
                print("submit_section: evaluations (non-serializable)=", results_out)
        except Exception:
            # don't break flow if logging fails
            pass

        cursor.execute("""
            INSERT INTO test_attempts (
                candidate_id, question_set_id, results_data
            )
            VALUES (%s, %s, %s)
            ON CONFLICT (candidate_id, question_set_id)
            DO UPDATE SET
                results_data = COALESCE(test_attempts.results_data, '[]'::jsonb) || EXCLUDED.results_data,
                video_url = COALESCE(EXCLUDED.video_url, test_attempts.video_url),
                audio_url = COALESCE(EXCLUDED.audio_url, test_attempts.audio_url),
                qa_data = COALESCE(test_attempts.qa_data, '[]'::jsonb);
        """, (
            uuid.UUID(candidate_id),
            uuid.UUID(question_set_id),
            json.dumps(results_out)
        ))

        conn.commit()

        return jsonify({"message": "Section stored", "evaluations": results_out}), 200

    except Exception as e:
        print("🔥 submit_section error:", e)
        return jsonify({"error": str(e)}), 500

    finally:
        if cursor: cursor.close()
        if conn: conn.close()

# ==============================================
# Save Full Test Details (role, skills, exp, schedule)
# ==============================================
@test_bp.route("/test/save_details", methods=["POST"])
def save_test_details():
    data = request.get_json() or {}

    candidate_id = data.get("candidate_id") or str(uuid.uuid4())
    question_set_id = data.get("question_set_id") or str(uuid.uuid4())

    role_title = data.get("role_title")
    skills = data.get("skills")
    experience = data.get("experience")
    work_arrangement = data.get("work_arrangement")
    location = data.get("location")
    annual_compensation = data.get("annual_compensation")

    test_start = data.get("test_start")  # expect ISO8601 or postgres-parsable
    test_end = data.get("test_end")

    conn = None
    cur = None
    try:
        conn = get_db_connection()
        cur = conn.cursor()

        cur.execute("""
            INSERT INTO candidate_test_details (
                candidate_id, question_set_id,
                role_title, skills, experience,
                work_arrangement, location, annual_compensation,
                test_start, test_end
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (candidate_id, question_set_id)
            DO UPDATE SET
                role_title = EXCLUDED.role_title,
                skills = EXCLUDED.skills,
                experience = EXCLUDED.experience,
                work_arrangement = EXCLUDED.work_arrangement,
                location = EXCLUDED.location,
                annual_compensation = EXCLUDED.annual_compensation,
                test_start = EXCLUDED.test_start,
                test_end = EXCLUDED.test_end;
        """, (
            uuid.UUID(candidate_id),
            uuid.UUID(question_set_id),
            role_title, skills, experience,
            work_arrangement, location, annual_compensation,
            test_start, test_end
        ))

        conn.commit()
        return jsonify({"message": "Test details saved successfully", "candidate_id": candidate_id, "question_set_id": question_set_id}), 200

    except Exception as e:
        print("🔥 save_details error:", e)
        return jsonify({"error": str(e)}), 500

    finally:
        if cur: cur.close()
        if conn: conn.close()

# ==============================================
# Save Generated Questions
# ==============================================
@test_bp.route("/questions/save", methods=["POST"])
def save_generated_questions():
    data = request.get_json() or {}

    question_set_id = data.get("question_set_id") or str(uuid.uuid4())
    questions = data.get("questions", [])

    if not isinstance(questions, list):
        return jsonify({"error": "questions must be a list"}), 400

    conn = None
    cur = None
    try:
        conn = get_db_connection()
        cur = conn.cursor()

        for q in questions:
            qid = uuid.uuid4()
            # ensure content is stored under a consistent shape
            content = q.get("content") if isinstance(q, dict) else q
            # allow top-level fields like type/skill to be at root
            entry = {
                "type": q.get("type"),
                "skill": q.get("skill"),
                "difficulty": q.get("difficulty"),
                "time_limit": q.get("time_limit"),
                "positive_marking": q.get("positive_marking"),
                "negative_marking": q.get("negative_marking"),
                "content": content
            }

            cur.execute("""
                INSERT INTO questions (id, question_set_id, content)
                VALUES (%s, %s, %s)
            """, (
                qid,
                uuid.UUID(question_set_id),
                json.dumps(entry)
            ))

        conn.commit()
        return jsonify({"message": "Questions saved successfully", "question_set_id": question_set_id}), 200

    except Exception as e:
        print("🔥 Error saving questions:", e)
        return jsonify({"error": str(e)}), 500

    finally:
        if cur: cur.close()
        if conn: conn.close()

# ==============================================
# Optional: Create session - returns candidate_id & question_set_id
# ==============================================
@test_bp.route("/test/create_session", methods=["POST"])
def create_session():
    data = request.get_json() or {}
    candidate_id = data.get("candidate_id") or str(uuid.uuid4())
    question_set_id = data.get("question_set_id") or str(uuid.uuid4())

    try:
        conn = get_db_connection()
        cur = conn.cursor()
        # create a placeholder row in test_attempts so ON CONFLICT works later
        cur.execute("""
            INSERT INTO test_attempts (candidate_id, question_set_id)
            VALUES (%s, %s)
            ON CONFLICT (candidate_id, question_set_id) DO NOTHING
        """, (uuid.UUID(candidate_id), uuid.UUID(question_set_id)))
        conn.commit()

        return jsonify({"candidate_id": candidate_id, "question_set_id": question_set_id}), 200

    except Exception as e:
        print("🔥 create_session error:", e)
        return jsonify({"error": str(e)}), 500

    finally:
        if 'cur' in locals(): cur.close()
        if 'conn' in locals(): conn.close()
