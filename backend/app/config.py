from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[2]

class Settings(BaseSettings):
    dashscope_api_key: str = ''
    qwen_base_url: str = 'https://dashscope.aliyuncs.com/compatible-mode/v1'
    qwen_model: str = 'qwen-plus'
    data_dir: Path = ROOT / 'data'
    max_upload_mb: int = 20
    model_config = SettingsConfigDict(env_file=ROOT / '.env', extra='ignore')

settings = Settings()
