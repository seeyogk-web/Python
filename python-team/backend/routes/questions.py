# File: routes/questions.py
# Backend route handlers for question generation and finalization

from flask import Blueprint, request, jsonify
from services.generator import generate_questions
import traceback
from config import get_db_connection
from utils.ids import gen_uuid 
import datetime
import json

questions_bp = Blueprint("questions", __name__)

@questions_bp.route("/finalise/finalized-test", methods=["GET"])
def get_finalized_test():
    candidate_id = request.args.get("candidateId")
    # job_id is not required; search all assessments for candidate_id presence
    conn = None
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        # Fetch all assessments
        cur.execute("""
            SELECT title, work_type, created_at, candidate_id, job_id, company, skills, location, question_set_id, exam_date, end_date, test_end, test_start
            FROM assessment_questions
        """)
        rows = cur.fetchall()
        matching_tests = []
        for row in rows:
            db_candidate_ids = row[3]
            if db_candidate_ids:
                candidate_id_list = [cid.strip() for cid in db_candidate_ids.split(",")]
                if candidate_id in candidate_id_list:
                    # Parse skills as array if not None
                    skills_val = row[6]
                    if skills_val:
                        if isinstance(skills_val, str):
                            skills_list = [s.strip() for s in skills_val.split(",") if s.strip()]
                        else:
                            skills_list = []
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
        else:
            # Return a single object with all fields as null/empty
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
        print("Error fetching assessment:", e)
        return jsonify({"error": str(e)}), 500
    finally:
        if conn: conn.close()


@questions_bp.route("/question-set/<question_set_id>/assessment", methods=["GET"])
def get_assessment_by_qset(question_set_id):
    """Return basic assessment metadata (title, role_title, company) for a question_set_id."""
    conn = None
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT title, role_title, company FROM assessment_questions WHERE question_set_id = %s LIMIT 1", (question_set_id,))
        row = cur.fetchone()
        if not row:
            return jsonify({"status": "error", "message": "Not found"}), 404

        title, role_title, company = row
        return jsonify({
            "status": "success",
            "title": title,
            "role_title": role_title,
            "company": company
        }), 200
    except Exception as e:
        print(f"Error fetching assessment metadata: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500
    finally:
        if conn: conn.close()


@questions_bp.route("/finalise/finalized-tests", methods=["GET"])
def get_all_finalized_tests():
    """Return all finalized tests (no candidateId filter)."""
    conn = None
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("""
            SELECT title, work_type, created_at, candidate_id, job_id, company, skills, location, question_set_id, exam_date, end_date, test_end, test_start
            FROM assessment_questions
        """)
        rows = cur.fetchall()
        out = []
        for row in rows:
            # parse skills into list when stored as comma-separated string
            skills_val = row[6]
            if skills_val:
                if isinstance(skills_val, str):
                    skills_list = [s.strip() for s in skills_val.split(",") if s.strip()]
                else:
                    skills_list = skills_val if isinstance(skills_val, list) else []
            else:
                skills_list = []

            out.append({
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
            })

        return jsonify(out), 200
    except Exception as e:
        print("Error fetching all assessments:", e)
        return jsonify({"error": str(e)}), 500
    finally:
        if conn: conn.close()


@questions_bp.route("/finalise/finalized-test/<question_set_id>", methods=["DELETE"])
def delete_finalized_test(question_set_id):
    """Delete an assessment by question_set_id."""
    conn = None
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        # Delete from assessment_questions; consider cascade or manual deletions if other tables reference it
        cur.execute("""
            DELETE FROM assessment_questions WHERE question_set_id = %s
        """, (question_set_id,))
        deleted = cur.rowcount
        conn.commit()
        if deleted:
            return jsonify({"message": "Deleted"}), 200
        else:
            return jsonify({"error": "Not found"}), 404
    except Exception as e:
        print("Error deleting assessment:", e)
        if conn:
            conn.rollback()
        return jsonify({"error": str(e)}), 500
    finally:
        if conn: conn.close()

@questions_bp.route("/generate-test", methods=["POST"])
def generate_test():
    """Generate questions based on skill selections"""
    data = request.get_json()

    if not data or "skills" not in data:
        return jsonify({"error": "Invalid request, missing skills"}), 400

    try:
        questions = generate_questions(data)
        # Return questions array wrapped in response object
        return jsonify({
            "status": "success", 
            "questions": questions
        }), 200
    except Exception as e:
        print("Error generating test:", str(e))
        traceback.print_exc()
        return jsonify({
            "status": "error",
            "message": str(e)
        }), 500

@questions_bp.route("/question-set/<question_set_id>/questions", methods=["GET"])
def get_questions(question_set_id):
    conn = None
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT content FROM questions WHERE question_set_id = %s", (question_set_id,))
        rows = cur.fetchall()
        questions = [
            r[0] if isinstance(r[0], dict) else json.loads(r[0])
            for r in rows
        ]
        return jsonify({
            "status": "success",
            "question_set_id": question_set_id,
            "questions": questions
        }), 200
    except Exception as e:
        print(e)
        return jsonify({"status": "error", "message": str(e)}), 500
    finally:
        if conn: conn.close()


@questions_bp.route("/question-set/<question_set_id>/question/<question_id>", methods=["GET"])
def get_question_by_id(question_set_id, question_id):
    """Return a single question object from `questions` table matching question_set_id and question_id.
    The `content` column is expected to be a JSON-serialized question object that contains a `question_id` field.
    If multiple rows exist for the same question_set_id, each row's content is searched.
    """
    conn = None
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT content FROM questions WHERE question_set_id = %s", (question_set_id,))
        rows = cur.fetchall()

        for r in rows:
            raw = r[0]
            try:
                # content may be stored as dict or JSON string
                qobj = raw if isinstance(raw, dict) else json.loads(raw)
            except Exception:
                # skip malformed rows
                continue

            # match by question_id field inside the question object
            qid = qobj.get('question_id') or qobj.get('id') or qobj.get('questionId')
            if qid and str(qid) == str(question_id):
                return jsonify({"status": "success", "question": qobj}), 200

        return jsonify({"status": "error", "message": "Question not found"}), 404
    except Exception as e:
        print(f"Error fetching question by id: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500
    finally:
        if conn: conn.close()

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
    
    # Log received data structure
    if data:
        print(f"Received keys: {list(data.keys())}")
        print(f"Data type: {type(data)}")
    else:
        print("ERROR: No data received")
        return jsonify({"error": "No data received"}), 400
    
    # Validate required fields
    if "questions" not in data:
        print("ERROR: Missing 'questions' in request data")
        print(f"Received data: {json.dumps(data, indent=2)}")
        return jsonify({"error": "Invalid request, missing questions"}), 400
    
    questions = data["questions"]
    
    # Validate questions is a list
    if not isinstance(questions, list):
        print(f"ERROR: 'questions' is not a list, type: {type(questions)}")
        return jsonify({"error": "Questions must be an array"}), 400
    
    if len(questions) == 0:
        print("ERROR: Questions array is empty")
        return jsonify({"error": "Questions array is empty"}), 400
    
    print(f"Number of questions received: {len(questions)}")
    
    # Extract test metadata
    test_title = data.get("test_title", "Untitled Test")
    test_description = data.get("test_description", "")
    job_id = data.get("job_id")
    
    print(f"Test Title: {test_title}")
    print(f"Test Description: {test_description}")
    print(f"Job ID: {job_id}")
    
    # Log first question structure for debugging
    if questions:
        print("\nFirst question structure:")
        print(json.dumps(questions[0], indent=2))

    conn = None
    try:
        print("\nAttempting to connect to database...")
        conn = get_db_connection()
        cur = conn.cursor()
        print("✓ Database connection successful")

        # Generate unique question_set_id
        question_set_id = gen_uuid()
        print(f"Generated question_set_id: {question_set_id}")
        
        # Calculate total duration
        total_duration = sum(q.get("time_limit", 60) for q in questions)
        print(f"Calculated total duration: {total_duration} seconds")

        # Set timestamps
        created_at = datetime.datetime.utcnow()

        # ✅ Use user selected end date/time if provided
        exam_date = data.get("startDate")
        start_time = data.get("startTime")

        print("aneesh", data)
        end_date = data.get("endDate")
        end_time = data.get("endTime")

        print(f"Received exam date: {exam_date}, start time: {start_time}")
        print(f"Received end date: {end_date}, end time: {end_time}")

        # ✅ Build expiry_time correctly
        if end_date and end_time:
            end_time_24 = convert_ampm_to_24h(end_time)
            if end_time_24:
                expiry_time = datetime.datetime.fromisoformat(f"{end_date}T{end_time_24}:00")
            else:
                expiry_time = created_at + datetime.timedelta(hours=48)
        else:
            # fallback to 48 hours if not provided
            expiry_time = created_at + datetime.timedelta(hours=48)
        
        print("Parsed times:")
        print("end_time_24:", end_time_24 if end_date else None)
        print(f"Created at: {created_at}")
        print(f"Expires at (final): {expiry_time}")

        # Insert into generated_questions table
        print("\nInserting into generated_questions table...")
        try:
            # Try with title and description columns
            cur.execute("""
                INSERT INTO generated_questions (id, job_id, title, description, duration, created_at, expiry_time)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
            """, (question_set_id, job_id, test_title, test_description, total_duration, created_at, expiry_time))
            print("✓ generated_questions inserted with title and description")
        except Exception as col_error:
            print(f"Warning: Could not insert with title/description: {col_error}")
            print("Attempting fallback insert without title/description...")
            conn.rollback()
            cur.execute("""
                INSERT INTO generated_questions (id, job_id, duration, created_at, expiry_time)
                VALUES (%s, %s, %s, %s, %s)
            """, (question_set_id, job_id, total_duration, created_at, expiry_time))
            print("✓ generated_questions inserted (basic format)")

        # Insert into assessment_questions table
        print("\nInserting into assessment_questions table...")
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
            print("✓ assessment_questions inserted with all fields")
        except Exception as aq_error:
            print(f"Warning: Could not insert into assessment_questions: {aq_error}")
            conn.rollback()

        # Insert questions
        print(f"\nProcessing {len(questions)} questions...")
        for i, q in enumerate(questions, 1):
            print(f"\n--- Processing question {i}/{len(questions)} ---")
            
            # Validate question structure
            required_fields = ['type', 'skill', 'difficulty', 'content']
            missing_fields = [field for field in required_fields if field not in q]
            
            if missing_fields:
                error_msg = f"Question {i} missing required fields: {missing_fields}"
                print(f"ERROR: {error_msg}")
                print(f"Question data: {json.dumps(q, indent=2)}")
                raise ValueError(error_msg)
            
            # Validate content is a dict
            if not isinstance(q['content'], dict):
                error_msg = f"Question {i} content must be a dictionary, got {type(q['content'])}"
                print(f"ERROR: {error_msg}")
                raise ValueError(error_msg)
            
            print(f"  Type: {q['type']}")
            print(f"  Skill: {q['skill']}")
            print(f"  Difficulty: {q['difficulty']}")
            print(f"  Content keys: {list(q['content'].keys())}")
            
            # Get or generate question_id
            question_id = q.get("question_id", gen_uuid())
            print(f"  Question ID: {question_id}")
            
            try:
                cur.execute("""
                    INSERT INTO questions (
                        question_set_id, content, created_at
                    )
                    VALUES (%s, %s, %s)
                """, (
                    str(question_set_id),
                    json.dumps(q),
                    created_at
                ))
                print(f"  ✓ Question {i} inserted successfully")
            except Exception as insert_error:
                print(f"  ERROR inserting question {i}: {str(insert_error)}")
                print(f"  Question data: {json.dumps(q, indent=2)}")
                raise

        print("\n" + "-"*50)
        print("Committing transaction...")
        conn.commit()
        cur.close()
        print("✓ Transaction committed successfully")

        print(f"\n{'='*50}")
        print(f"✓✓✓ SUCCESS ✓✓✓")
        print(f"Test '{test_title}' finalized")
        print(f"Question Set ID: {question_set_id}")
        print(f"Questions stored: {len(questions)}")
        print("="*50 + "\n")

        return jsonify({
            "status": "success",
            "question_set_id": question_set_id,
            "test_title": test_title,
            "expiry_time": expiry_time.isoformat(),
            "message": f"Test '{test_title}' finalized and stored successfully"
        }), 201

    except ValueError as ve:
        # Validation errors
        print(f"\n❌ VALIDATION ERROR: {str(ve)}")
        if conn:
            conn.rollback()
        return jsonify({
            "status": "error", 
            "message": str(ve),
            "error": "Validation failed"
        }), 400
        
    except Exception as e:
        # Database or other errors
        print(f"\n❌ ERROR: {str(e)}")
        print("Full traceback:")
        traceback.print_exc()
        
        if conn:
            print("Rolling back transaction...")
            conn.rollback()
            
        return jsonify({
            "status": "error", 
            "message": str(e),
            "error": "Database operation failed"
        }), 500

    finally:
        if conn:
            print("Closing database connection...")
            conn.close()
        print("="*50 + "\n")