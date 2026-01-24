# File: routes/questions.py
# Backend route handlers for question generation and finalization

from flask import Blueprint, request, jsonify
from services.generator import generate_questions
import traceback
from config import get_db_connection
from utils.ids import gen_uuid
import datetime
import json
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

questions_bp = Blueprint("questions", __name__)

@questions_bp.route("/finalise/finalized-test", methods=["GET"])
def get_finalized_test():
    candidate_id = request.args.get("candidateId")
    conn = None
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("""
            SELECT title, work_type, created_at, candidate_id, job_id, company, skills, location, question_set_id, exam_date, end_date, test_end, test_start
            FROM assessment_questions
        """)
        rows = cur.fetchall()
        matching_tests = []
        for row in rows:
            db_candidate_ids = row[3]
            if not db_candidate_ids:
                continue
            candidate_id_list = [cid.strip() for cid in str(db_candidate_ids).split(",") if cid.strip()]
            if candidate_id not in candidate_id_list:
                continue

            skills_val = row[6]
            if isinstance(skills_val, str):
                skills_list = [s.strip() for s in skills_val.split(",") if s.strip()]
            else:
                skills_list = []

            test = {
                "title": row[0],
                "workType": row[1],
                "createdAt": row[2].isoformat() if row[2] else None,
                "candidate_id": row[3],
                "job_id": row[4],
                "company": row[5],
                "skills": skills_list,
                "location": row[7],
                "question_set_id": row[8],
                "exam_date": row[9],
                "end_date": row[10],
                "test_end": row[11],
                "test_start": row[12]
            }
            matching_tests.append(test)

        if matching_tests:
            return jsonify(matching_tests), 200

        # empty result
        return jsonify([{
            "title": None,
            "workType": None,
            "createdAt": None,
            "candidate_id": None,
            "job_id": None,
            "company": None,
            "skills": [],
            "location": None,
            "question_set_id": None
        }]), 200
    except Exception as e:
        logger.exception("Error fetching assessment")
        return jsonify({"error": str(e)}), 500
    finally:
        try:
            if cur:
                cur.close()
        except Exception:
            pass
        if conn:
            conn.close()

@questions_bp.route("/generate-test", methods=["POST"])
def generate_test():
    """Generate questions based on skill selections"""
    data = request.get_json()

    if not data or "skills" not in data:
        return jsonify({"error": "Invalid request, missing skills"}), 400

    try:
        questions = generate_questions(data)
        return jsonify({"status": "success", "questions": questions}), 200
    except Exception as e:
        logger.exception("Error generating test")
        return jsonify({"status": "error", "message": str(e)}), 500

@questions_bp.route("/question-set/<question_set_id>/questions", methods=["GET"])
def get_questions(question_set_id):
    conn = None
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT content FROM questions WHERE question_set_id = %s", (question_set_id,))
        rows = cur.fetchall()
        questions = []
        for r in rows:
            val = r[0]
            if isinstance(val, dict):
                questions.append(val)
            else:
                try:
                    questions.append(json.loads(val))
                except Exception:
                    questions.append(val)

        return jsonify({"status": "success", "question_set_id": question_set_id, "questions": questions}), 200
    except Exception as e:
        logger.exception("Error fetching questions for question_set_id=%s", question_set_id)
        return jsonify({"status": "error", "message": str(e)}), 500
    finally:
        try:
            if cur:
                cur.close()
        except Exception:
            pass
        if conn:
            conn.close()

def convert_ampm_to_24h(time_str):
    if not time_str:
        return None
    try:
        time_str = time_str.strip().upper()
        in_time = datetime.datetime.strptime(time_str, "%I:%M %p")
        return in_time.strftime("%H:%M")
    except ValueError:
        return None

@questions_bp.route("/finalize-test", methods=["POST"])
def finalize_test():
    """Finalize test and store in database"""
    print("\n" + "="*50)
    print("BACKEND: FINALIZE TEST REQUEST RECEIVED")
    print("="*50)
    
    data = request.get_json()

    if not data:
        logger.error("No data received in finalize_test request")
        return jsonify({"error": "No data received"}), 400

    logger.info("Finalize test request received, keys=%s", list(data.keys()))
    
    if "questions" not in data:
        logger.error("Missing 'questions' in request data: %s", json.dumps(data)[:200])
        return jsonify({"error": "Invalid request, missing questions"}), 400

    questions = data["questions"]
    if not isinstance(questions, list):
        logger.error("'questions' is not a list, type=%s", type(questions))
        return jsonify({"error": "Questions must be an array"}), 400

    if len(questions) == 0:
        logger.error("Questions array is empty")
        return jsonify({"error": "Questions array is empty"}), 400

    logger.info("Number of questions received: %d", len(questions))
    
    # Extract test metadata
    test_title = data.get("test_title", "Untitled Test")
    test_description = data.get("test_description", "")
    job_id = data.get("job_id")

    logger.info("Test Title: %s; job_id=%s", test_title, job_id)
    
    # Log first question structure for debugging
    if questions:
        logger.debug("First question structure: %s", json.dumps(questions[0]))

    conn = None
    cur = None
    try:
        logger.info("Connecting to database")
        conn = get_db_connection()
        cur = conn.cursor()
        logger.info("Database connection successful")

        # Generate unique question_set_id
        question_set_id = gen_uuid()
        logger.info("Generated question_set_id: %s", question_set_id)
        
        # Calculate total duration
        total_duration = sum(q.get("time_limit", 60) for q in questions)
        logger.info("Calculated total duration: %s seconds", total_duration)

        # Set timestamps
        created_at = datetime.datetime.utcnow()

        # ✅ Use user selected end date/time if provided
        exam_date = data.get("startDate")
        start_time = data.get("startTime")

        end_date = data.get("endDate")
        end_time = data.get("endTime")
        logger.debug("Received exam date: %s, start time: %s", exam_date, start_time)
        logger.debug("Received end date: %s, end time: %s", end_date, end_time)

        # ✅ Build expiry_time correctly
        end_time_24 = None
        if end_date and end_time:
            end_time_24 = convert_ampm_to_24h(end_time)
            if end_time_24:
                expiry_time = datetime.datetime.fromisoformat(f"{end_date}T{end_time_24}:00")
            else:
                expiry_time = created_at + datetime.timedelta(hours=48)
        else:
            expiry_time = created_at + datetime.timedelta(hours=48)

        logger.info("Expiry time calculated: %s (end_time_24=%s)", expiry_time, end_time_24)

        # Insert into generated_questions table
        logger.info("Inserting into generated_questions table")
        try:
            # Try with title and description columns
            cur.execute("""
                INSERT INTO generated_questions (id, job_id, title, description, duration, created_at, expiry_time)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
            """, (question_set_id, job_id, test_title, test_description, total_duration, created_at, expiry_time))
            logger.info("generated_questions inserted with title and description")
        except Exception as col_error:
            logger.warning("Could not insert with title/description: %s", col_error)
            logger.info("Attempting fallback insert without title/description")
            conn.rollback()
            cur.execute("""
                INSERT INTO generated_questions (id, job_id, duration, created_at, expiry_time)
                VALUES (%s, %s, %s, %s, %s)
            """, (question_set_id, job_id, total_duration, created_at, expiry_time))
            logger.info("generated_questions inserted (basic format)")

        # Insert into assessment_questions table
        logger.info("Inserting into assessment_questions table")
        company = data.get("company", "Unknown Company")
        location = data.get("location", "Remote")
        work_type = data.get("workType", "Full-time")
        # Prepare all fields for insertion
        role_title = data.get("role_title")
        skills = data.get("skills")
        experience = data.get("experience")
        work_arrangement = data.get("work_arrangement")
        annual_compensation = data.get("annual_compensation")
        test_start = data.get("test_start")
        test_end = data.get("test_end")
        question_type = data.get("question_type")
        difficulty = data.get("difficulty")
        skill = data.get("skill")
        metadata = data.get("metadata")
        candidate_id = data.get("candidate_ids")
        job_id = data.get("job_id")

        try:
            cur.execute("""
                INSERT INTO assessment_questions (
                    question_set_id, title, company, location, work_type, created_at,
                    role_title, skills, experience, work_arrangement, annual_compensation,
                    test_start, test_end, exam_date, end_date, question_type, difficulty, skill, metadata, candidate_id, job_id
                ) VALUES (
                    %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
                )
            """,
            (
                question_set_id, test_title, company, location, work_type, created_at,
                role_title, json.dumps(skills) if skills is not None else None, experience, work_arrangement, annual_compensation,
                start_time, end_time, exam_date, end_date, question_type, difficulty, skill, metadata, candidate_id, job_id
            ))
            logger.info("assessment_questions inserted with all fields")
        except Exception as aq_error:
            logger.warning("Could not insert into assessment_questions: %s", aq_error)
            conn.rollback()

        # Insert questions
        logger.info("Processing %d questions", len(questions))
        for i, q in enumerate(questions, 1):
            logger.debug("Processing question %d/%d", i, len(questions))

            required_fields = ["type", "skill", "difficulty", "content"]
            missing_fields = [field for field in required_fields if field not in q]
            if missing_fields:
                error_msg = f"Question {i} missing required fields: {missing_fields}"
                logger.error(error_msg)
                raise ValueError(error_msg)

            if not isinstance(q["content"], dict):
                error_msg = f"Question {i} content must be a dictionary, got {type(q['content'])}"
                logger.error(error_msg)
                raise ValueError(error_msg)

            logger.debug("Question %d: type=%s skill=%s difficulty=%s", i, q["type"], q["skill"], q["difficulty"])

            question_id = q.get("question_id", gen_uuid())

            try:
                cur.execute("""
                    INSERT INTO questions (
                        question_set_id, content, created_at
                    )
                    VALUES (%s, %s, %s)
                """, (str(question_set_id), json.dumps(q), created_at))
                logger.debug("Question %d inserted", i)
            except Exception as insert_error:
                logger.exception("Error inserting question %d", i)
                raise

        logger.info("Committing transaction")
        conn.commit()
        cur.close()
        logger.info("Transaction committed successfully")

        logger.info("SUCCESS: Test '%s' finalized, question_set_id=%s, stored=%d", test_title, question_set_id, len(questions))

        return jsonify({"status": "success", "question_set_id": question_set_id, "test_title": test_title, "expiry_time": expiry_time.isoformat(), "message": f"Test '{test_title}' finalized and stored successfully"}), 201

    except ValueError as ve:
        logger.error("VALIDATION ERROR: %s", ve)
        if conn:
            conn.rollback()
        return jsonify({"status": "error", "message": str(ve), "error": "Validation failed"}), 400

    except Exception as e:
        logger.exception("ERROR finalizing test")
        if conn:
            logger.info("Rolling back transaction")
            conn.rollback()
        return jsonify({"status": "error", "message": str(e), "error": "Database operation failed"}), 500

    finally:
        try:
            if cur:
                cur.close()
        except Exception:
            pass
        if conn:
            logger.info("Closing database connection")
            conn.close()
        logger.info("finalize_test complete")