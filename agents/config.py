from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    ollama_model: str = "llama3.2"
    ollama_base_url: str = "http://localhost:11434"
    redis_url: str = "redis://localhost:6379/0."
    mcp_server_dir: str = "servers"
    app_port: int = 8000
    streamlit_port: int = 8501

    model_config = {"env_file": ".env"}


settings = Settings()
