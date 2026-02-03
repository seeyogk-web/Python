from flask import Blueprint, request, jsonify, send_from_directory
import re
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

def full_media_url(path):
    """Return an absolute URL for a media path stored in DB.

    - If `path` is already absolute (http/https) return as-is.
    - If `path` starts with `/` prepend the request host_url.
    - Otherwise prepend host_url and a slash.
    Returns None for falsy `path`.
    """
    if not path:
        print(f"full_media_url: input is falsy -> {path}")
        return None
    try:
        # already absolute
        if path.startswith("http://") or path.startswith("https://"):
            print(f"full_media_url: already absolute -> {path}")
            return path
    except Exception:
        pass

    # Include the application's script_root (mounting/blueprint prefix) so
    # generated URLs point to the correct mounted path (e.g. /ai/v1).
    script_root = (request.script_root or "").rstrip("/")
    # If script_root is not set (some environments), try to infer the prefix
    # from the request path (take everything before '/test'). This handles
    # cases where the blueprint is mounted under a prefix like '/ai/v1'.
    if not script_root:
        try:
            m = re.search(r'^(.*?)/test(/|$)', request.path)
            if m:
                script_root = m.group(1).rstrip("/")
        except Exception:
            script_root = ""

    host = request.host_url.rstrip("/")
    base = f"{host}{script_root}"
    if path.startswith("/"):
        resolved = f"{base}{path}"
        print(f"full_media_url: resolved -> {resolved}")
        return resolved
    resolved = f"{base}/{path}"
    print(f"full_media_url: resolved -> {resolved}")
    return resolved

test_bp = Blueprint("test", __name__)


@test_bp.route(f"/{UPLOAD_DIR}/<path:filename>", methods=["GET"])
@test_bp.route("ai/recordings/<path:filename>", methods=["GET"])
def serve_recording(filename):
    """Serve uploaded recordings from the backend `recordings` folder.

    Supports both `/recordings/<file>` and `/ai/recordings/<file>` URL shapes.
    """
    folder = os.path.abspath(UPLOAD_DIR)
    full_path = os.path.join(folder, filename)
    try:
        exists = os.path.exists(full_path)
        print(f"serve_recording request -> folder={folder} filename={filename} exists={exists}")
        if not exists:
            print("serve_recording: file not found on disk:", full_path)
            return jsonify({"error": "File not found", "path": full_path}), 404
        return send_from_directory(folder, filename)
    except Exception as e:
        print("🔥 serve_recording error:", e)
        return jsonify({"error": "File not found"}), 404


@test_bp.route("/test/video_url", methods=["GET"])
def get_video_url_for_attempt():
    """Return a full video URL for an attempt by `attempt_id` or `candidate_id` query param.

    Query params accepted: `attempt_id`, `candidate_id`, `candidateId`.
    """
    attempt_id = (
        request.args.get("attempt_id")
        or request.args.get("candidate_id")
        or request.args.get("candidateId")
    )
    if not attempt_id:
        return jsonify({"error": "attempt_id or candidate_id required"}), 400

    conn = None
    cur = None
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        # Compare `id` as text to avoid invalid UUID cast when a candidate_id-style
        # string (e.g. 'candidate_...') is provided. This prevents Postgres from
        # throwing "invalid input syntax for type uuid" errors.
        cur.execute(
            "SELECT video_url FROM test_attempts WHERE id::text = %s OR candidate_id = %s LIMIT 1",
            (attempt_id, attempt_id),
        )
        row = cur.fetchone()
        if not row:
            return jsonify({"video_url": None}), 200

        video_path = row[0]
        resolved = full_media_url(video_path)
        print(f"get_video_url: attempt_id={attempt_id} video_path={video_path} resolved={resolved}")
        return jsonify({"video_url": resolved}), 200

    except Exception as e:
        print("🔥 get_video_url error:", e)
        return jsonify({"error": str(e)}), 500

    finally:
        if cur:
            try:
                cur.close()
            except Exception:
                pass
        if conn:
            try:
                conn.close()
            except Exception:
                pass




def _ensure_candidate_taken_table(conn):
    """Ensure the candidate_test_taken table exists."""
    cur = None
    try:
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS candidate_test_taken (
                id SERIAL PRIMARY KEY,
                candidate_id VARCHAR(255) NOT NULL,
                job_id VARCHAR(255),
                question_set_id VARCHAR(255) NOT NULL,
                cid VARCHAR(255),
                taken_at TIMESTAMPTZ DEFAULT NOW()
            )
        """)
        # create an index to speed up lookups
        cur.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS candidate_test_taken_unique_idx
            ON candidate_test_taken (candidate_id, job_id, question_set_id)
        """)
        # Ensure columns exist on older schemas: add cid, job_id, taken_at if missing
        try:
            cur.execute("ALTER TABLE candidate_test_taken ADD COLUMN IF NOT EXISTS cid VARCHAR(255)")
            cur.execute("ALTER TABLE candidate_test_taken ADD COLUMN IF NOT EXISTS job_id VARCHAR(255)")
            cur.execute("ALTER TABLE candidate_test_taken ADD COLUMN IF NOT EXISTS taken_at TIMESTAMPTZ DEFAULT NOW()")
        except Exception:
            # non-fatal: continue if ALTER fails for any reason
            try:
                conn.rollback()
            except Exception:
                pass
        conn.commit()
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
    finally:
        if cur: cur.close()


@test_bp.route("/test/taken", methods=["GET"])
def taken_tests():
    """Return a list of taken tests (job_id, question_set_id) for a candidate.

    Query parameters:
      - candidate_id or cid
    """
    conn = None
    cur = None
    try:
        conn = get_db_connection()
        _ensure_candidate_taken_table(conn)
        candidate_id = request.args.get('candidate_id') or request.args.get('cid')
        if not candidate_id:
            return jsonify({'error': 'candidate_id (or cid) required'}), 400

        cur = conn.cursor()
        cur.execute(
            "SELECT job_id, question_set_id, cid, taken_at FROM candidate_test_taken WHERE candidate_id = %s OR cid = %s",
            (candidate_id, candidate_id)
        )
        rows = cur.fetchall()
        taken = []
        for r in rows:
            job_id, question_set_id, cid_val, taken_at = r
            taken.append({
                'job_id': job_id,
                'question_set_id': question_set_id,
                'cid': cid_val,
                'taken_at': taken_at.isoformat() if hasattr(taken_at, 'isoformat') else taken_at,
            })
        return jsonify({'taken': taken}), 200
    except Exception as e:
        print('🔥 taken_tests error:', e)
        return jsonify({'error': str(e)}), 500
    finally:
        if cur: cur.close()
        if conn: conn.close()
# ==============================================
# Start Test
# ==============================================
@test_bp.route("/test/start/<question_set_id>", methods=["GET"])
def start_test(question_set_id):
    conn = None
    cursor = None
    try:
        conn = get_db_connection()
        # If candidate_id provided, ensure candidate hasn't already taken this test
        candidate_id = request.args.get("candidate_id") or request.args.get("cid")
        job_id = request.args.get("job_id") or request.args.get("jobId")
        if candidate_id:
            try:
                _ensure_candidate_taken_table(conn)
                chk_cur = conn.cursor()
                if job_id:
                    chk_cur.execute(
                        "SELECT 1 FROM candidate_test_taken WHERE candidate_id = %s AND question_set_id = %s AND job_id = %s LIMIT 1",
                        (candidate_id, question_set_id, job_id)
                    )
                else:
                    chk_cur.execute(
                        "SELECT 1 FROM candidate_test_taken WHERE candidate_id = %s AND question_set_id = %s LIMIT 1",
                        (candidate_id, question_set_id)
                    )
                row = chk_cur.fetchone()
                chk_cur.close()
                if row:
                    return jsonify({"error": "Test already taken"}), 403
            except Exception:
                try:
                    conn.rollback()
                except Exception:
                    pass
        cursor = conn.cursor()

        cursor.execute("""
            SELECT id, content
            FROM questions
            WHERE question_set_id = %s
        """, (question_set_id,))
        rows = cursor.fetchall()

        questions_list = []

        for qid, raw in rows:
            # qid may be UUID object
            qid_str = str(qid)
            raw_json = json.loads(raw) if isinstance(raw, str) else raw

            question_type = (
                raw_json.get("type")
                or raw_json.get("content", {}).get("type")
            )

            inner = raw_json.get("content", {})

            question_type = raw_json.get("type") or raw_json.get("content", {}).get("type")

            questions_list.append({
                "id": qid_str,
                "question_id": qid_str,
                "type": question_type,
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
# List Attempts (for reports)
# ==============================================
@test_bp.route("/test/attempts", methods=["GET"])
def list_attempts():
    conn = None
    cur = None
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("""
            SELECT id, candidate_id, question_set_id, results_data, qa_data,
                   audio_url, video_url, tab_switches, inactivities, face_not_visible, cid, created_at
            FROM test_attempts
            ORDER BY created_at DESC NULLS LAST
            LIMIT 1000
        """)
        rows = cur.fetchall()

        attempts = []
        for r in rows:
            (rid, candidate_id, question_set_id, results_data, qa_data,
             audio_url, video_url, tab_switches, inactivities, face_not_visible, cid, created_at) = r

            def _maybe_parse(v):
                if v is None: return None
                if isinstance(v, str):
                    try:
                        return json.loads(v)
                    except Exception:
                        return v
                return v

            attempts.append({
                "id": str(rid) if rid is not None else None,
                "candidate_id": candidate_id,
                "question_set_id": str(question_set_id) if question_set_id is not None else None,
                "results_data": _maybe_parse(results_data),
                "qa_data": _maybe_parse(qa_data),
                "audio_url": audio_url,
                "video_url": video_url,
                "tab_switches": tab_switches,
                "inactivities": inactivities,
                "face_not_visible": face_not_visible,
                "cid": cid,
                "created_at": created_at.isoformat() if hasattr(created_at, 'isoformat') else created_at,
            })

        return jsonify({"attempts": attempts}), 200
    except Exception as e:
        print("🔥 list_attempts error:", e)
        return jsonify({"error": str(e)}), 500
    finally:
        if cur: cur.close()
        if conn: conn.close()


@test_bp.route("/test/attempts/<attempt_id>", methods=["GET", "DELETE", "OPTIONS"])
def attempt_detail(attempt_id):
        # handle preflight
        if request.method == "OPTIONS":
            return ('', 204)

        conn = None
        cur = None
        try:
            conn = get_db_connection()
            cur = conn.cursor()

            if request.method == 'DELETE':
                cur.execute(
                    """
                    DELETE FROM test_attempts
                    WHERE id = %s OR candidate_id = %s
                    RETURNING id
                    """,
                    (attempt_id, attempt_id)
                )
                row = cur.fetchone()
                if not row:
                    return jsonify({"error": "Attempt not found"}), 404
                conn.commit()
                return jsonify({"deleted": True, "id": str(row[0])}), 200

            # GET: try treating the path param as a question_set_id and return
            # all attempts for that question_set. If none are found, fall back
            # to fetching a single attempt by id or candidate_id (existing behavior).
            cur.execute("""
                SELECT id, candidate_id, question_set_id, results_data, qa_data,
                       audio_url, video_url, tab_switches, inactivities, face_not_visible, cid, created_at
                FROM test_attempts
                WHERE question_set_id = %s
                ORDER BY created_at DESC
                LIMIT 1000
            """, (attempt_id,))

            rows = cur.fetchall()
            if not rows:
                return jsonify({"message": "No data for this test"}), 200
            if rows:
                attempts = []
                for r in rows:
                    (rid, candidate_id, question_set_id, results_data, qa_data,
                     audio_url, video_url, tab_switches, inactivities, face_not_visible, cid, created_at) = r

                    def _maybe_parse(v):
                        if v is None: return None
                        if isinstance(v, str):
                            try:
                                return json.loads(v)
                            except Exception:
                                return v
                        return v

                    attempts.append({
                        "id": str(rid) if rid is not None else None,
                        "candidate_id": candidate_id,
                        "question_set_id": str(question_set_id) if question_set_id is not None else None,
                        "results_data": _maybe_parse(results_data),
                        "qa_data": _maybe_parse(qa_data),
                        "audio_url": audio_url,
                        "video_url": video_url,
                        "tab_switches": tab_switches,
                        "inactivities": inactivities,
                        "face_not_visible": face_not_visible,
                        "cid": cid,
                        "created_at": created_at.isoformat() if hasattr(created_at, 'isoformat') else created_at,
                    })

                return jsonify(attempts), 200

            # GET fallback: fetch single attempt by id or candidate_id
            cur.execute("""
                SELECT id, candidate_id, question_set_id, results_data, qa_data,
                       audio_url, video_url, tab_switches, inactivities, face_not_visible, cid, created_at
                FROM test_attempts
                WHERE id = %s OR candidate_id = %s
                LIMIT 1
            """, (attempt_id, attempt_id))

            row = cur.fetchone()
            if not row:
                return jsonify({"error": "Attempt not found"}), 404

            (rid, candidate_id, question_set_id, results_data, qa_data,
             audio_url, video_url, tab_switches, inactivities, face_not_visible, cid, created_at) = row

            def _maybe_parse(v):
                if v is None: return None
                if isinstance(v, str):
                    try:
                        return json.loads(v)
                    except Exception:
                        return v
                return v

            attempt = {
                "id": str(rid) if rid is not None else None,
                "candidate_id": candidate_id,
                "question_set_id": str(question_set_id) if question_set_id is not None else None,
                "results_data": _maybe_parse(results_data),
                "qa_data": _maybe_parse(qa_data),
                "audio_url": audio_url,
                "video_url": video_url,
                "tab_switches": tab_switches,
                "inactivities": inactivities,
                "face_not_visible": face_not_visible,
                "cid": cid,
                "created_at": created_at.isoformat() if hasattr(created_at, 'isoformat') else created_at,
            }

            return jsonify(attempt), 200

        except Exception as e:
            print("🔥 attempt_detail error:", e)
            return jsonify({"error": str(e)}), 500
        finally:
            if cur: cur.close()
            if conn: conn.close()

# ==============================================
# Save Violations
# ==============================================
@test_bp.route("/test/save_violations", methods=["POST"])
def save_violations():
    data = request.get_json() or {}
    print("save_violations called")
    try:
        print("Request JSON:", data)
    except Exception:
        print("Request JSON (non-serializable):", data)
    candidate_id = data.get("candidate_id")
    question_set_id = data.get("question_set_id")
    tab_switches = data.get("tab_switches", 0)
    inactivities = data.get("inactivities", 0)
    face_not_visible = data.get("face_not_visible", 0)
    cid = data.get("cid")

    print(f"Parsed: candidate_id={candidate_id} question_set_id={question_set_id} tab_switches={tab_switches} inactivities={inactivities} face_not_visible={face_not_visible} cid={cid}")

    if not candidate_id or not question_set_id:
        return jsonify({"error": "candidate_id and question_set_id required"}), 400

    conn = None
    cur = None
    try:
        conn = get_db_connection()
        cur = conn.cursor()

        # Try updating an existing attempt row first; if none updated, insert a new row.
        try:
            cur.execute("""
                UPDATE test_attempts
                SET tab_switches = %s,
                    inactivities = %s,
                    face_not_visible = %s,
                    cid = COALESCE(%s, cid)
                WHERE candidate_id = %s AND question_set_id = %s
            """, (
                tab_switches,
                inactivities,
                face_not_visible,
                cid,
                candidate_id,
                question_set_id
            ))

            if cur.rowcount == 0:
                cur.execute("""
                    INSERT INTO test_attempts (
                        candidate_id, question_set_id,
                        tab_switches, inactivities, face_not_visible, cid
                    ) VALUES (%s, %s, %s, %s, %s, %s)
                """, (
                    candidate_id,
                    question_set_id,
                    tab_switches,
                    inactivities,
                    face_not_visible,
                    cid
                ))
        except Exception as e:
            msg = str(e).lower()
            if 'column "cid" does not exist' in msg or 'cid' in msg and 'does not exist' in msg:
                try:
                    conn.rollback()
                except Exception:
                    pass
                cur.execute("""
                    UPDATE test_attempts
                    SET tab_switches = %s,
                        inactivities = %s,
                        face_not_visible = %s
                    WHERE candidate_id = %s AND question_set_id = %s
                """, (
                    tab_switches,
                    inactivities,
                    face_not_visible,
                    candidate_id,
                    question_set_id
                ))

                if cur.rowcount == 0:
                    cur.execute("""
                        INSERT INTO test_attempts (
                            candidate_id, question_set_id,
                            tab_switches, inactivities, face_not_visible, cid
                        ) VALUES (%s, %s, %s, %s, %s, %s)
                    """, (
                        candidate_id,
                        question_set_id,
                        tab_switches,
                        inactivities,
                        face_not_visible,
                        cid
                    ))
            else:
                raise

        conn.commit()
        try:
            print("test_attempts updated/inserted; committed. Current tab_switches:", tab_switches)
        except Exception:
            pass
        # Mark test as completed so candidate cannot retake.
        # Insert into candidate_test_taken unconditionally (based on this violations call),
        # avoiding duplicates by checking existence first.
        try:
            try:
                _ensure_candidate_taken_table(conn)
                ins_cur = conn.cursor()
                job_id = data.get("job_id") or data.get("jobId")
                ins_cur.execute(
                    "SELECT 1 FROM candidate_test_taken WHERE (candidate_id = %s OR cid = %s) AND question_set_id = %s LIMIT 1",
                    (candidate_id, cid, question_set_id)
                )
                exists = ins_cur.fetchone()
                if not exists:
                    ins_cur.execute(
                        "INSERT INTO candidate_test_taken (candidate_id, job_id, question_set_id, cid) VALUES (%s, %s, %s, %s)",
                        (candidate_id, job_id, question_set_id, cid)
                    )
                    conn.commit()
                    print(f"Inserted candidate_test_taken: candidate_id={candidate_id} job_id={job_id} question_set_id={question_set_id} cid={cid}")
                else:
                    print("candidate_test_taken entry already exists for candidate/question_set; no insert performed.")
                try:
                    ins_cur.close()
                except Exception:
                    pass
            except Exception as ex_ins:
                print("Error while inserting into candidate_test_taken:", ex_ins)
                try:
                    conn.rollback()
                except Exception:
                    pass
        except Exception:
            pass

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
        cid = request.form.get("cid")

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
        print(f"upload_audio: saved -> {save_path} audio_url={audio_url}")

        conn = get_db_connection()
        cur = conn.cursor()

        # Try to update existing attempt row first; otherwise insert.
        # If the DB doesn't have a `cid` column, fall back to SQL without it.
        try:
            cur.execute("""
                UPDATE test_attempts
                SET audio_url = COALESCE(%s, audio_url),
                    qa_data = COALESCE(test_attempts.qa_data, '[]'::jsonb) || %s::jsonb,
                    cid = COALESCE(%s, cid)
                WHERE candidate_id = %s AND question_set_id = %s
            """, (
                audio_url,
                json.dumps(qa_data),
                cid,
                candidate_id,
                question_set_id,
            ))

            if cur.rowcount == 0:
                cur.execute("""
                    INSERT INTO test_attempts (
                        candidate_id, question_set_id, audio_url, qa_data, cid
                    ) VALUES (%s, %s, %s, %s, %s)
                """, (
                    candidate_id,
                    question_set_id,
                    audio_url,
                    json.dumps(qa_data),
                    cid
                ))
        except Exception as e:
            msg = str(e).lower()
            if 'column "cid" does not exist' in msg or 'cid' in msg and 'does not exist' in msg:
                # Rollback the failed statement and retry without cid column
                try:
                    conn.rollback()
                except Exception:
                    pass
                cur.execute("""
                    UPDATE test_attempts
                    SET audio_url = COALESCE(%s, audio_url),
                        qa_data = COALESCE(test_attempts.qa_data, '[]'::jsonb) || %s::jsonb
                    WHERE candidate_id = %s AND question_set_id = %s
                """, (
                    audio_url,
                    json.dumps(qa_data),
                    candidate_id,
                    question_set_id,
                ))

                if cur.rowcount == 0:
                    cur.execute("""
                        INSERT INTO test_attempts (
                            candidate_id, question_set_id, audio_url, qa_data
                        ) VALUES (%s, %s, %s, %s)
                    """, (
                        candidate_id,
                        question_set_id,
                        audio_url,
                        json.dumps(qa_data)
                    ))
            else:
                raise

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
        cid = request.form.get("cid")

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
        print(f"upload_video: saved -> {save_path} video_url={video_url}")

        conn = get_db_connection()
        cur = conn.cursor()

        # Try to update an existing attempt row first; otherwise insert.
        # If `cid` column is missing, fall back to SQL without it.
        try:
            cur.execute("""
                UPDATE test_attempts
                SET video_url = COALESCE(%s, video_url),
                    qa_data = COALESCE(test_attempts.qa_data, '[]'::jsonb) || %s::jsonb,
                    cid = COALESCE(%s, cid)
                WHERE candidate_id = %s AND question_set_id = %s
            """, (
                video_url,
                json.dumps(qa_data),
                cid,
                candidate_id,
                question_set_id,
            ))

            if cur.rowcount == 0:
                cur.execute("""
                    INSERT INTO test_attempts (
                        candidate_id, question_set_id, video_url, qa_data, cid
                    ) VALUES (%s, %s, %s, %s, %s)
                """, (
                    candidate_id,
                    question_set_id,
                    video_url,
                    json.dumps(qa_data),
                    cid
                ))
        except Exception as e:
            msg = str(e).lower()
            if 'column "cid" does not exist' in msg or 'cid' in msg and 'does not exist' in msg:
                try:
                    conn.rollback()
                except Exception:
                    pass
                cur.execute("""
                    UPDATE test_attempts
                    SET video_url = COALESCE(%s, video_url),
                        qa_data = COALESCE(test_attempts.qa_data, '[]'::jsonb) || %s::jsonb
                    WHERE candidate_id = %s AND question_set_id = %s
                """, (
                    video_url,
                    json.dumps(qa_data),
                    candidate_id,
                    question_set_id,
                ))

                if cur.rowcount == 0:
                    cur.execute("""
                        INSERT INTO test_attempts (
                            candidate_id, question_set_id, video_url, qa_data
                        ) VALUES (%s, %s, %s, %s)
                    """, (
                        candidate_id,
                        question_set_id,
                        video_url,
                        json.dumps(qa_data)
                    ))
            else:
                raise

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
@test_bp.route("/test/submit_section", methods=["POST", "OPTIONS"])
@test_bp.route("/test/submit_section/<question_set_id>", methods=["POST", "OPTIONS"])
def submit_section(question_set_id=None):
    # handle preflight
    if request.method == "OPTIONS":
        return ('', 204)

    data = request.get_json() or {}

    # prefer path parameter if supplied, otherwise body
    question_set_id = question_set_id or data.get("question_set_id")
    section_name = data.get("section_name")
    responses = data.get("responses", [])
    candidate_id = data.get("candidate_id")
    cid = data.get("cid")

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

            # include question text where possible (prefer submitted question_text, otherwise metadata)
            try:
                q_meta = question_meta.get(str(qid)) if question_meta else {}
            except Exception:
                q_meta = {}

            question_text_val = None
            try:
                question_text_val = qtext or q_meta.get('question') or q_meta.get('prompt_text') or q_meta.get('q_text')
            except Exception:
                question_text_val = qtext

            results_out.append({
                "question_id": qid,
                "question": question_text_val,
                "candidate_answer": answer,
                "correct_answer": correct,
                "section_name": section_name,
                "score": evaluation.get("score"),
                "is_correct": evaluation.get("is_correct"),
                "feedback": evaluation.get("feedback"),
                "positive_marking": pos_mark
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

        # conn and cursor already open above
        try:
            cursor.execute("""
                UPDATE test_attempts
                SET results_data = COALESCE(test_attempts.results_data, '[]'::jsonb) || %s::jsonb,
                    video_url = COALESCE(%s, video_url),
                    audio_url = COALESCE(%s, audio_url),
                    qa_data = COALESCE(test_attempts.qa_data, '[]'::jsonb),
                    cid = COALESCE(%s, cid)
                WHERE candidate_id = %s AND question_set_id = %s
            """, (
                json.dumps(results_out),
                None,
                None,
                cid,
                candidate_id,
                question_set_id,
            ))

            if cursor.rowcount == 0:
                cursor.execute("""
                    INSERT INTO test_attempts (
                        candidate_id, question_set_id, results_data, cid
                    ) VALUES (%s, %s, %s, %s)
                """, (
                    candidate_id,
                    question_set_id,
                    json.dumps(results_out),
                    cid
                ))
        except Exception as e:
            msg = str(e).lower()
            if 'column "cid" does not exist' in msg or 'cid' in msg and 'does not exist' in msg:
                try:
                    conn.rollback()
                except Exception:
                    pass
                cursor.execute("""
                    UPDATE test_attempts
                    SET results_data = COALESCE(test_attempts.results_data, '[]'::jsonb) || %s::jsonb,
                        video_url = COALESCE(%s, video_url),
                        audio_url = COALESCE(%s, audio_url),
                        qa_data = COALESCE(test_attempts.qa_data, '[]'::jsonb)
                    WHERE candidate_id = %s AND question_set_id = %s
                """, (
                    json.dumps(results_out),
                    None,
                    None,
                    candidate_id,
                    question_set_id,
                ))

                if cursor.rowcount == 0:
                    cursor.execute("""
                        INSERT INTO test_attempts (
                            candidate_id, question_set_id, results_data
                        ) VALUES (%s, %s, %s)
                    """, (
                        candidate_id,
                        question_set_id,
                        json.dumps(results_out)
                    ))
            else:
                raise

        conn.commit()

        # Optionally mark test as completed for this candidate so they cannot retake
        try:
            mark_complete = data.get("mark_complete") or data.get("final") or data.get("is_last_section")
            job_id = data.get("job_id") or data.get("jobId")
            if mark_complete:
                try:
                    print('submit_section: mark_complete detected; details ->', {
                        'candidate_id': candidate_id,
                        'cid': cid,
                        'question_set_id': question_set_id,
                        'job_id': job_id,
                        'mark_complete': mark_complete,
                        'data_keys': list(data.keys())
                    })
                    _ensure_candidate_taken_table(conn)
                    chk = conn.cursor()
                    # Insert only if not already present for this candidate (or cid) and question_set
                    chk.execute(
                        "SELECT 1 FROM candidate_test_taken WHERE (candidate_id = %s OR cid = %s) AND question_set_id = %s LIMIT 1",
                        (candidate_id, cid, question_set_id)
                    )
                    exists = chk.fetchone()
                    if not exists:
                        try:
                            chk.execute(
                                "INSERT INTO candidate_test_taken (candidate_id, job_id, question_set_id, cid) VALUES (%s, %s, %s, %s)",
                                (candidate_id, job_id, question_set_id, cid)
                            )
                            conn.commit()
                            print(f"Inserted candidate_test_taken: candidate_id={candidate_id} job_id={job_id} question_set_id={question_set_id} cid={cid}")
                        except Exception as ins_ex:
                            print('submit_section: insert into candidate_test_taken failed:', ins_ex)
                            try:
                                conn.rollback()
                            except Exception:
                                pass
                    else:
                        print('submit_section: candidate_test_taken entry already exists for candidate/question_set; no insert performed.')
                    try:
                        chk.close()
                    except Exception:
                        pass
                except Exception as e:
                    print('submit_section: error while handling mark_complete ->', e)
                    try:
                        conn.rollback()
                    except Exception:
                        pass
        except Exception:
            pass

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

        # Update if exists, else insert (avoid relying on ON CONFLICT index)
        cur.execute("""
            UPDATE candidate_test_details
            SET role_title = %s,
                skills = %s,
                experience = %s,
                work_arrangement = %s,
                location = %s,
                annual_compensation = %s,
                test_start = %s,
                test_end = %s
            WHERE candidate_id = %s AND question_set_id = %s
        """, (
            role_title, skills, experience,
            work_arrangement, location, annual_compensation,
            test_start, test_end,
            candidate_id, question_set_id
        ))
        if cur.rowcount == 0:
            cur.execute("""
                INSERT INTO candidate_test_details (
                    candidate_id, question_set_id,
                    role_title, skills, experience,
                    work_arrangement, location, annual_compensation,
                    test_start, test_end
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, (
                candidate_id,
                question_set_id,
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
    cid = data.get("cid")

    try:
        conn = get_db_connection()
        cur = conn.cursor()
        # create a placeholder row in test_attempts if none exists
        cur.execute("""
            SELECT 1 FROM test_attempts WHERE candidate_id = %s AND question_set_id = %s
        """, (candidate_id, question_set_id))
        if cur.fetchone() is None:
            try:
                cur.execute("""
                    INSERT INTO test_attempts (candidate_id, question_set_id, cid)
                    VALUES (%s, %s, %s)
                """, (candidate_id, question_set_id, cid))
                conn.commit()
            except Exception as e:
                msg = str(e).lower()
                if 'column "cid" does not exist' in msg or 'cid' in msg and 'does not exist' in msg:
                    try:
                        conn.rollback()
                    except Exception:
                        pass
                    cur.execute("""
                        INSERT INTO test_attempts (candidate_id, question_set_id)
                        VALUES (%s, %s)
                    """, (candidate_id, question_set_id))
                    conn.commit()
                else:
                    raise

        return jsonify({"candidate_id": candidate_id, "question_set_id": question_set_id}), 200

    except Exception as e:
        print("🔥 create_session error:", e)
        return jsonify({"error": str(e)}), 500

    finally:
        if 'cur' in locals(): cur.close()
        if 'conn' in locals(): conn.close()