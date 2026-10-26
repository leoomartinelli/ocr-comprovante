import logging
import sqlite3
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, File, Query, Request, Response, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.arquivo import detectar_tipo, eh_pdf
from app.config import Configuracoes, obter_configuracoes
from app.erros import ErroApi
from app.metricas import Evento, obter_metricas
from app.motor_ocr import obter_motor
from app.schemas import RespostaErro, RespostaSucesso
from app.seguranca import exigir_api_key
from app.servico import Resultado, processar_detalhado

logging.basicConfig(
    level=obter_configuracoes().log_level,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
log = logging.getLogger(__name__)

ROTA_OCR = "/ocr/comprovante"
PAINEL_HTML = Path(__file__).with_name("painel.html")
ERRO_INTERNO = "Erro interno ao processar o comprovante."


@asynccontextmanager
async def _ciclo_de_vida(_: FastAPI) -> AsyncIterator[None]:
    obter_motor()  # carrega o modelo na subida: o primeiro print não paga esse custo
    yield


app = FastAPI(title="OCR de comprovantes Pix", version="0.1.0", lifespan=_ciclo_de_vida)


def _erro(request: Request, status_code: int, message: str) -> JSONResponse:
    request.state.motivo_erro = message  # o painel agrupa os erros por esta mensagem
    return JSONResponse(RespostaErro(message=message).model_dump(), status_code=status_code)


@app.exception_handler(ErroApi)
async def _tratar_erro_api(request: Request, exc: ErroApi) -> JSONResponse:
    return _erro(request, exc.status_code, exc.message)


@app.exception_handler(RequestValidationError)
async def _tratar_validacao(request: Request, __: RequestValidationError) -> JSONResponse:
    return _erro(request, 400, "Envie o arquivo no campo multipart 'arquivo'.")


@app.exception_handler(StarletteHTTPException)
async def _tratar_http(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    return _erro(request, exc.status_code, str(exc.detail))


@app.exception_handler(Exception)
async def _tratar_inesperado(request: Request, exc: Exception) -> JSONResponse:
    log.error("erro inesperado: %s", type(exc).__name__)
    return _erro(request, 500, ERRO_INTERNO)


def _evento(request: Request, status: int, duracao_ms: int) -> Evento:
    resultado: Resultado | None = getattr(request.state, "resultado", None)
    if status != 200 or resultado is None:
        motivo = getattr(request.state, "motivo_erro", None) or ERRO_INTERNO
        return Evento(status=status, duracao_ms=duracao_ms, motivo=motivo)
    d = resultado.dados
    presentes = {
        "valor": d.valor is not None,
        "id_transacao": bool(d.id_transacao),
        "data_hora": d.data_hora is not None,
        "favorecido": bool(d.favorecido.nome),
        "pagador": bool(d.pagador.nome),
        "banco": bool(d.banco),
        "identificador": bool(d.identificador),
    }
    return Evento(
        status=status,
        duracao_ms=duracao_ms,
        metodo=d.metodo,
        metodo_original=d.metodo_original,
        confianca=d.confianca,
        revisao=d.requer_revisao_humana,
        valor=d.valor,
        banco=d.banco,
        encontrados=[campo for campo, achou in presentes.items() if achou],
        campos_incertos=d.campos_incertos,
        tentativas=resultado.tentativas_ocr,
        llm=resultado.acionou_llm,
        tokens_entrada=resultado.llm_tokens_entrada,
        tokens_saida=resultado.llm_tokens_saida,
    )


def _registrar(request: Request, status: int, inicio: float) -> None:
    metricas = obter_metricas(obter_configuracoes())
    if metricas is None:
        return
    try:
        metricas.registrar(_evento(request, status, int((time.monotonic() - inicio) * 1000)))
    except (sqlite3.Error, OSError) as exc:  # métrica nunca derruba a resposta ao n8n
        log.warning("falha ao registrar métrica: %s", type(exc).__name__)


@app.middleware("http")
async def _medir(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
    if request.method != "POST" or request.url.path != ROTA_OCR:
        return await call_next(request)
    inicio, status = time.monotonic(), 500
    try:
        resposta = await call_next(request)
        status = resposta.status_code
        return resposta
    finally:
        await run_in_threadpool(_registrar, request, status, inicio)


@app.get("/saude")
def saude() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/painel", response_class=HTMLResponse, include_in_schema=False)
def painel() -> HTMLResponse:
    # a página não traz dados; ela pede a API key e busca /painel/dados
    return HTMLResponse(PAINEL_HTML.read_text(encoding="utf-8"))


@app.get("/painel/dados", dependencies=[Depends(exigir_api_key)])
def painel_dados(
    dias: int = Query(30, ge=1, le=365),
    config: Configuracoes = Depends(obter_configuracoes),
) -> dict:
    metricas = obter_metricas(config)
    if metricas is None:
        raise ErroApi(404, "Métricas desligadas (METRICAS_ATIVO=false).")
    return {**metricas.resumo(dias), "limiar": config.confianca_minima}


@app.post(
    ROTA_OCR,
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
    request: Request,
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

    resultado = processar_detalhado(conteudo, config)
    request.state.resultado = resultado
    return RespostaSucesso(data=resultado.dados)
