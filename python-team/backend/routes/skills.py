from flask import Blueprint, jsonify

skills_bp = Blueprint("skills", __name__)

@skills_bp.route("/skills", methods=["GET"])
def get_skills():
    # Dummy data - replace with Node API call later
    skills = [
        {"id": 1, "name": "JavaScript"},
        {"id": 2, "name": "React"},
        {"id": 3, "name": "Python"},
        {"id": 4, "name": "SQL"}
    ]
    return jsonify({"skills": skills})
