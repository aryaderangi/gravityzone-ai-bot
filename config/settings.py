import os
from dotenv import load_dotenv

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENV_FILE = os.path.join(BASE_DIR, ".env")

load_dotenv(dotenv_path=ENV_FILE, override=True)

DATABASE_URL = os.getenv("DATABASE_URL")

NODE1_URL = os.getenv("NODE1_URL")
NODE2_URL = os.getenv("NODE2_URL")
NODE3_URL = os.getenv("NODE3_URL")

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
GROK_API_KEY = os.getenv("GROK_API_KEY")
