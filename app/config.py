import os
from typing import Optional

class Settings:
    BASE_URL: Optional[str] = os.getenv("BASE_URL")
    AI_PROVIDER: str = os.getenv("AI_PROVIDER", "auto")
    
    # Gemini
    GEMINI_API_KEY: Optional[str] = os.getenv("GEMINI_API_KEY")
    GEMINI_MODEL: str = os.getenv("GEMINI_MODEL", "gemini-2.0-flash")
    
    # OpenAI / Compatible
    OPENAI_API_KEY: Optional[str] = os.getenv("OPENAI_API_KEY")
    OPENAI_MODEL: str = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
    OPENAI_BASE_URL: str = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
    
    # Database
    DATABASE_PATH: str = os.getenv("DATABASE_PATH", "invoice_agent.db")
    
    # Limits
    MAX_PAYLOAD_BYTES: int = 512 * 1024  # 512 KiB

settings = Settings()
