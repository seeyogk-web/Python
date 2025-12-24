from flask import Flask
from routes import bp as api_bp
from sqlalchemy import create_engine
import os

def create_app():
    app = Flask(__name__)
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise RuntimeError("Please set DATABASE_URL in environment (or .env)")
    engine = create_engine(database_url, future=True)
    app.config["DB_ENGINE"] = engine
    app.register_blueprint(api_bp, url_prefix="/api")
    return app

if __name__ == "__main__":
    app = create_app()
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5000)), debug=True)
