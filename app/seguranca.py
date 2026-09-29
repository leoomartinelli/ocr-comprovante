from hmac import compare_digest

from fastapi import Depends, Security
from fastapi.security import APIKeyHeader

from app.config import Configuracoes, obter_configuracoes
from app.erros import ErroApi

_cabecalho = APIKeyHeader(name="X-API-Key", auto_error=False)


def exigir_api_key(
    chave: str | None = Security(_cabecalho),
    config: Configuracoes = Depends(obter_configuracoes),
) -> None:
    if not chave or not compare_digest(chave.encode(), config.api_key.encode()):
        raise ErroApi(401, "API key ausente ou inválida.")
