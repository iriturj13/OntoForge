import os
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    POSTGRES_HOST: str = "localhost"
    POSTGRES_PORT: int = 5432
    POSTGRES_DB: str = "ontoforge_metadata"
    POSTGRES_USER: str = "postgres"
    POSTGRES_PASSWORD: str = "postgres"
    
    CUBEJS_URL: str = "http://localhost:4000"
    CUBEJS_API_SECRET: str = "cubejs_secret_token"
    
    DATA_DIR: str = "d:/Antigravity/OntoForge/data"
    
    # Toggle to fallback to SQLite if PostgreSQL connection fails
    USE_SQLITE_FALLBACK: bool = True
    
    class Config:
        env_file = ".env"
        extra = "ignore"

settings = Settings()
