from datetime import datetime
from typing import Literal

from pydantic import BaseModel


class Pagador(BaseModel):
    nome: str | None = None
    documento: str | None = None


class Favorecido(BaseModel):
    nome: str | None = None
    documento: str | None = None
    chave_pix: str | None = None


class DadosComprovante(BaseModel):
    valor: float | None = None
    data_hora: datetime | None = None
    id_transacao: str | None = None
    pagador: Pagador = Pagador()
    favorecido: Favorecido = Favorecido()
    banco: str | None = None
    identificador: str | None = None
    confianca: float = 0.0
    campos_incertos: list[str] = []
    requer_revisao_humana: bool = True
    metodo: Literal["ocr_local", "ocr_local+llm", "cache"]
    metodo_original: Literal["ocr_local", "ocr_local+llm"] | None = None
    hash_arquivo: str
    texto_bruto: str | None = None


class RespostaSucesso(BaseModel):
    success: Literal[True] = True
    data: DadosComprovante


class RespostaErro(BaseModel):
    success: Literal[False] = False
    message: str
