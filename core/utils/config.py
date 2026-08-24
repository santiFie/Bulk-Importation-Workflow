"""
Configuración centralizada para la aplicación Multi-Services Router
Gestiona variables de entorno y configuración global
"""

import os
from dotenv import load_dotenv
from typing import Optional

# Cargar variables de entorno
load_dotenv()


class Config:
    """Clase principal de configuración"""
    
    # ==================== GENERAL ====================
    ENVIRONMENT: str = os.getenv("ENVIRONMENT", "development")
    DEBUG: bool = ENVIRONMENT == "development"
    
    # ==================== LLM PROVIDERS ====================
    # Google Gemini
    GEMINI_API_KEY: str = os.getenv("GEMINI_API_KEY", "")
    
    # Groq
    GROQ_API_KEY: str = os.getenv("GROQ_API_KEY", "")
    
    # NVIDIA
    NVIDIA_API_KEY: str = os.getenv("NVIDIA_API_KEY", "")
    
    # HuggingFace
    HF_TOKEN: str = os.getenv("HF_TOKEN", "")
    
    # ==================== SEARCH & RAG ====================
    TAVILY_API_KEY: Optional[str] = os.getenv("TAVILY_API_KEY")
    TAVILY_MAX_RESULTS: int = 3
    
    # RAG Configuration
    RAG_PDF_PATH: str = os.getenv("RAG_PDF_PATH", "./rag_pdfs")
    CHUNK_SIZE: int = 1500
    CHUNK_OVERLAP: int = 200
    EMBEDDING_MODEL: str = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
    RETRIEVER_K: int = 5

    # OpenAlex MCP
    OPENALEX_API_KEY: str = os.getenv("OPENALEX_API_KEY", "")
    OPENALEX_EMAIL: str = os.getenv("OPENALEX_EMAIL", "")  # Polite pool: higher rate limits
    
    # ==================== LANGCHAIN ====================
    LANGCHAIN_TRACING_V2: str = os.getenv("LANGCHAIN_TRACING_V2", "false")
    LANGCHAIN_ENDPOINT: str = os.getenv("LANGCHAIN_ENDPOINT", "https://api.smith.langchain.com")
    LANGCHAIN_API_KEY: Optional[str] = os.getenv("LANGCHAIN_API_KEY")
    LANGCHAIN_PROJECT: str = os.getenv("LANGCHAIN_PROJECT", "Multi-Services-Router")
    
    # ==================== MCP SERVERS ====================
    # GitHub MCP
    GITHUB_TOKEN: Optional[str] = os.getenv("GITHUB_TOKEN")
    GITHUB_PERSONAL_ACCESS_TOKEN: Optional[str] = os.getenv("GITHUB_PERSONAL_ACCESS_TOKEN")
    DEFAULT_GITHUB_REPO: str = os.getenv("DEFAULT_GITHUB_REPO", "santiFie/LangGraph")
    
    # Filesystem MCP
    WORKSPACE_PATH: str = os.getenv("WORKSPACE_PATH", "/home/santi/Documentos/LangGraph/Modulo-Marta/")

    # Bots MCP (RAFA)
    BOTS_MCP_URL: str = (
        "http://localhost:9002/sse"
    )

    # DSpace MCP
    DSPACE_MCP_URL: str = (
        os.getenv("DSPACE_MCP_URL") 
        or "http://mcp:5000/sse"
    )

    MINIO_MCP_URL = "http://localhost:9005/sse"

    # ==================== METADATA EXTRACTOR ====================
    METADATA_EXTRACTOR_MCP_URL: str = os.getenv("METADATA_EXTRACTOR_MCP_URL", "")
    METADATA_EXTRACTOR_MODEL = os.getenv("METADATA_EXTRACTOR_MODEL", "openai/gpt-oss-120b")

    # ==================== ORCHESTRATOR ====================
    ORCHESTRATOR_API_KEY: str = os.getenv("ORCHESTRATOR_API_KEY", "")
    ORCHESTRATOR_BASE_URL: str = os.getenv("ORCHESTRATOR_BASE_URL", "")
    ORCHESTRATOR_PASSWORD: str = os.getenv("ORCHESTRATOR_PASSWORD", "")
    ORCHESTRATOR_LOCAL_API_KEY: str = os.getenv("ORCHESTRATOR_LOCAL_API_KEY", "")
    ORCHESTRATOR_BASE_URL_LOCAL: str = os.getenv("ORCHESTRATOR_BASE_URL_LOCAL", "")
    ORCHESTRATOR_MCP_URL: str = (
        os.getenv("ORCHESTRATOR_MCP_URL")
    )

    # ==================== DATABASE ====================
    DATABASE_URL: str = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///./checkpoint.db")
    CHECKPOINT_DB: str = os.getenv("CHECKPOINT_DB", "checkpoint.db")
    
    # ==================== SERVER ====================
    SERVER_HOST: str = os.getenv("SERVER_HOST", "0.0.0.0")
    SERVER_PORT: int = int(os.getenv("SERVER_PORT", "8000"))
    SERVER_RELOAD: bool = DEBUG
    
    # ==================== LOGGING ====================
    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO" if not DEBUG else "DEBUG")
    LOG_FORMAT: str = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    
    # ==================== AGENTS ====================
    SEARCHER_MODEL: str = "openai/gpt-oss-20b"
    GITHUB_MODEL: str =  "gemini-3.1-flash-lite-preview"
    SUPERVISOR_MODEL: str = "openai/gpt-oss-120b"
    BOTS_MODEL: str = "openai/gpt-oss-120b"
    DSPACE_MODEL = "google/gemma-4-31b-it"
    MINIO_MODEL = "openai/gpt-oss-120b"
    # OPENALEX_MODEL = "nvidia/nemotron-3-ultra-550b-a55b"
    OPENALEX_MODEL = "openai/gpt-oss-120b"
    CROSSWALK_MODEL = "openai/gpt-oss-20b"
    CROSSWALK_VALIDATOR_MODEL = "nvidia/nemotron-nano-12b-v2-vl:free"
    METADATA_EXTRACTOR_MODEL = "openai/gpt-oss-120b"
    
    OPEN_ROUTER_API_KEY = os.getenv("OPEN_ROUTER_API_KEY")
    OPEN_ROUTER_BASE_URL = "https://openrouter.ai/api/v1"

    # ==================== MINIO CONFIG ====================
    MINIO_MCP_DIR = os.path.join(WORKSPACE_PATH, "mcps", "minio_mcp")
    DOWNLOADS_DIR: str = os.path.join(MINIO_MCP_DIR, "Downloads")
    MINIO_ROOT_USER: str = os.getenv("MINIO_ROOT_USER", "admin")
    MINIO_ROOT_PASSWORD: str = os.getenv("MINIO_ROOT_PASSWORD", "admin123456")

    # ==================== CROSSWALK CONFIG ====================
    CROSSWALK_API_USERNAME: str = os.getenv("CROSSWALK_API_USERNAME", "")
    CROSSWALK_API_PASSWORD: str = os.getenv("CROSSWALK_API_PASSWORD", "")
    CROSSWALK_API_URL: str = os.getenv("CROSSWALK_API_URL", "http://localhost:8000")

    @classmethod
    def validate(cls) -> bool:
        """Validate required configuration and API keys"""
        required_keys = [
            "GEMINI_API_KEY",
            "GROQ_API_KEY",
            "TAVILY_API_KEY",
            "DSPACE_MODEL",
        ]
        
        missing = [key for key in required_keys if not getattr(cls, key)]
        
        if missing:
            print(f"Error: Environment variables missing: {', '.join(missing)}")
            return False
        
        return True
    
    @classmethod
    def to_dict(cls) -> dict:
        """Returns configuration as a dictionary"""
        return {
            key: getattr(cls, key)
            for key in dir(cls)
            if not key.startswith("_") and key.isupper()
        }


# Instancia singleton
config = Config()
