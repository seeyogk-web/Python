import os
from dotenv import load_dotenv
import psycopg2

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
OPENROUTER_URL = os.getenv("OPENROUTER_URL")
OPENROUTER_MODEL = os.getenv("OPENROUTER_MODEL")

if not OPENROUTER_API_KEY or not OPENROUTER_URL or not OPENROUTER_MODEL:
    raise ValueError("Please set OPENROUTER_API_KEY, OPENROUTER_URL, and OPENROUTER_MODEL in .env")

def get_db_connection():
    return psycopg2.connect(DATABASE_URL)