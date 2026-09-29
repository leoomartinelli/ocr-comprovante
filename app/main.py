import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, File, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.arquivo import detectar_tipo, eh_pdf
from app.config import Configuracoes, obter_configuracoes
from app.erros import ErroApi
from app.motor_ocr import obter_motor
from app.schemas import RespostaErro, RespostaSucesso
from app.seguranca import exigir_api_key
from app.servico import processar

logging.basicConfig(
    level=obter_configuracoes().log_level,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
log = logging.getLogger(__name__)


@asynccontextmanager
async def _ciclo_de_vida(_: FastAPI) -> AsyncIterator[None]:
    obter_motor()  # carrega o modelo na subida: o primeiro print não paga esse custo
    yield


app = FastAPI(title="OCR de comprovantes Pix", version="0.1.0", lifespan=_ciclo_de_vida)


def _erro(status_code: int, message: str) -> JSONResponse:
    return JSONResponse(RespostaErro(message=message).model_dump(), status_code=status_code)


@app.exception_handler(ErroApi)
async def _tratar_erro_api(_: Request, exc: ErroApi) -> JSONResponse:
    return _erro(exc.status_code, exc.message)


@app.exception_handler(RequestValidationError)
async def _tratar_validacao(_: Request, __: RequestValidationError) -> JSONResponse:
    return _erro(400, "Envie o arquivo no campo multipart 'arquivo'.")


@app.exception_handler(StarletteHTTPException)
async def _tratar_http(_: Request, exc: StarletteHTTPException) -> JSONResponse:
    return _erro(exc.status_code, str(exc.detail))


@app.exception_handler(Exception)
async def _tratar_inesperado(_: Request, exc: Exception) -> JSONResponse:
    log.error("erro inesperado: %s", type(exc).__name__)
    return _erro(500, "Erro interno ao processar o comprovante.")


@app.get("/saude")
def saude() -> dict[str, str]:
    return {"status": "ok"}


@app.post(
    "/ocr/comprovante",
    response_model=RespostaSucesso,
    dependencies=[Depends(exigir_api_key)],
    responses={
        400: {"model": RespostaErro},
        401: {"model": RespostaErro},
        413: {"model": RespostaErro},
        422: {"model": RespostaErro},
    },
)
def ocr_comprovante(
    arquivo: UploadFile = File(...),
    config: Configuracoes = Depends(obter_configuracoes),
) -> RespostaSucesso:
    conteudo = arquivo.file.read(config.tamanho_maximo_bytes + 1)
    if len(conteudo) > config.tamanho_maximo_bytes:
        raise ErroApi(413, f"Arquivo maior que {config.tamanho_maximo_mb}MB.")
    if not conteudo:
        raise ErroApi(400, "Arquivo vazio.")

    if eh_pdf(conteudo):
        raise ErroApi(400, "PDF não é aceito neste endpoint. Envie um print (JPG, PNG ou WEBP).")
    if detectar_tipo(conteudo) is None:
        raise ErroApi(400, "Tipo de arquivo não suportado. Envie JPG, PNG ou WEBP.")

    return RespostaSucesso(data=processar(conteudo, config))
