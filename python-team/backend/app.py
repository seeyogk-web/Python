from flask import Flask
from flask_cors import CORS   # 👈 import CORS
from routes.questions import questions_bp
from routes.skills import skills_bp
from routes.test import test_bp    # ✅ import test blueprint


def create_app():
    app = Flask(__name__)

    # ✅ Enable CORS for all routes and all origins
    CORS(app, resources={r"/*": {"origins": "*"}})

    # Register blueprints
    app.register_blueprint(questions_bp, url_prefix="/ai/v1")
    app.register_blueprint(skills_bp, url_prefix="/ai/v1")
    app.register_blueprint(test_bp, url_prefix="/ai/v1")   # ✅ register test routes

    @app.route("/")
    def home():
        return {"message": "Backend running"}

    return app


if __name__ == "__main__":
    app = create_app()
    app.run(debug=True, port=5000)
