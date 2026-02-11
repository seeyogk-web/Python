#!/usr/bin/env python3
"""Test script to diagnose LLM API issues"""
import logging
import sys
import os

# Setup logging to see all debug messages
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler('llm_test.log')
    ]
)

# Now import after logging is configured
from services.llm_client import generate_question
from config import OPENROUTER_API_KEY, OPENROUTER_URL, OPENROUTER_MODEL

logger = logging.getLogger(__name__)

def test_generate_question():
    """Test a single question generation"""
    logger.info("=" * 60)
    logger.info("Testing LLM Question Generation")
    logger.info("=" * 60)
    
    logger.info(f"API Key configured: {bool(OPENROUTER_API_KEY)}")
    logger.info(f"API URL: {OPENROUTER_URL}")
    logger.info(f"Model: {OPENROUTER_MODEL}")
    
    print("\n" + "=" * 60)
    print("Generating MCQ question for ReactJS...")
    print("=" * 60 + "\n")
    
    result = generate_question(
        skill="ReactJS",
        difficulty="medium",
        qtype="mcq",
        options=4
    )
    
    logger.info(f"Result: {result}")
    
    if result and result.get("question") and not result["question"].startswith("Generate a question"):
        print("✓ SUCCESS: Real question generated!")
        print(f"Question: {result['question']}")
        if result.get("options"):
            print(f"Options: {result['options']}")
    else:
        print("✗ FAILED: Got placeholder or None")
        print(f"Result: {result}")
    
    return result

if __name__ == "__main__":
    test_generate_question()
