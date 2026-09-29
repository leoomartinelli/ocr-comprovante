from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Configuracoes(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    api_key: str
    tamanho_maximo_mb: int = 10
    log_level: str = "INFO"

    # OCR
    confianca_minima: float = 0.75
    largura_minima_escala: int = 800
    ocr_concorrencia: int = 2

    # cache por hash (só hash + resultado; o arquivo original nunca é gravado)
    cache_ativo: bool = True
    cache_db_path: str = "dados/cache.db"
    cache_ttl_dias: int = 30

    # fallback LLM (camada 7): desligado por padrão
    llm_fallback_ativo: bool = False
    anthropic_api_key: str | None = None
    llm_modelo: str = "claude-haiku-4-5"
    llm_max_chars: int = 6000
    llm_timeout_segundos: float = 20.0

    @property
    def tamanho_maximo_bytes(self) -> int:
        return self.tamanho_maximo_mb * 1024 * 1024


@lru_cache
def obter_configuracoes() -> Configuracoes:
    return Configuracoes()
