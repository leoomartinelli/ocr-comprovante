import logging
import re
import threading
import time
from dataclasses import dataclass
from datetime import datetime

from app import llm
from app import validadores as val
from app.arquivo import calcular_hash
from app.cache import obter_cache
from app.config import Configuracoes
from app.erros import ErroApi
from app.leitura import TextoLido, ler_imagem
from app.motor_ocr import obter_motor
from app.parser import BRT, Extracao, achar_documento, datas_candidatas, interpretar
from app.parser import todos_os_valores as valores_do_texto
from app.pontuacao import Avaliacao, avaliar
from app.preprocessamento import preparar
from app.schemas import DadosComprovante

log = logging.getLogger(__name__)

TEXTO_MINIMO = 15
PROXIMA_ESCALA = {1.0: 2.0, 2.0: 3.0}
MAX_LEITURAS = 3
MIN_GANHO_ESCALA = 1.05

_semaforo: threading.BoundedSemaphore | None = None
_trava_semaforo = threading.Lock()


def _limite_ocr(config: Configuracoes) -> threading.BoundedSemaphore:
    global _semaforo
    with _trava_semaforo:
        if _semaforo is None:
            _semaforo = threading.BoundedSemaphore(config.ocr_concorrencia)
        return _semaforo


@dataclass
class Leitura:
    lido: TextoLido
    extracao: Extracao
    avaliacao: Avaliacao
    escala: float
    escala_pedida: float


@dataclass
class Resultado:
    dados: DadosComprovante
    tentativas_ocr: int
    acionou_llm: bool
    do_cache: bool
    llm_tokens_entrada: int = 0
    llm_tokens_saida: int = 0


def _ler(
    conteudo: bytes, config: Configuracoes, escala: float | None, superar: float | None = None
) -> Leitura | None:
    """Lê a imagem. Com `superar`, devolve None (sem gastar OCR) se a ampliação efetiva
    não passar da leitura anterior."""
    preparada = preparar(conteudo, largura_minima=config.largura_minima_escala, escala=escala)
    if superar is not None and preparada.escala < superar * MIN_GANHO_ESCALA:
        return None
    with _limite_ocr(config):
        lido = ler_imagem(obter_motor(), preparada)
    extracao = interpretar(lido.texto)
    avaliacao = avaliar(extracao, lido.confianca_media, config.confianca_minima)
    return Leitura(lido, extracao, avaliacao, preparada.escala, preparada.escala_pedida)


def _espacos_nos_nomes(e: Extracao) -> int:
    return sum((n or "").count(" ") for n in (e.favorecido.nome, e.pagador.nome))


def _qualidade(leitura: Leitura) -> tuple[float, int, int]:
    """Mais confiança, menos dúvidas e, no empate, mais espaços nos nomes (espaço perdido
    é o defeito mais comum do OCR)."""
    return (
        leitura.avaliacao.confianca,
        -len(leitura.avaliacao.campos_incertos),
        _espacos_nos_nomes(leitura.extracao),
    )


def _melhor(a: Leitura, b: Leitura) -> Leitura:
    return b if _qualidade(b) > _qualidade(a) else a


def _ausente_ou_duvidoso(campo: str, valor, e: Extracao) -> bool:
    return valor is None or campo in e.incertos


def _mesclar_llm(e: Extracao, sugestao: dict, texto: str) -> None:
    """Aplica a sugestão do LLM só onde o parser falhou ou duvidou, e só se o valor também
    estiver no texto do OCR (o LLM pode reorganizar, nunca inventar)."""
    linhas = [ln for ln in texto.splitlines() if ln.strip()]
    compacto = re.sub(r"[^A-Za-z0-9]", "", texto)
    normalizado = val.normalizar_nome(texto)

    valor = sugestao.get("valor")
    if _ausente_ou_duvidoso("valor", e.valor, e) and isinstance(valor, int | float):
        if round(float(valor), 2) in {round(v, 2) for v in valores_do_texto(linhas)}:
            e.valor = round(float(valor), 2)
            e.incertos.discard("valor")

    candidato = str(sugestao.get("id_transacao") or "")
    if _ausente_ou_duvidoso("id_transacao", e.id_transacao, e) and candidato in compacto:
        corrigido = val.corrigir_e2e(candidato)
        if corrigido:
            e.id_transacao, e.e2e_utc = corrigido, val.data_do_e2e(corrigido)

    try:
        data = datetime.fromisoformat(str(sugestao.get("data_hora")))
    except ValueError:
        data = None
    if data and _ausente_ou_duvidoso("data_hora", e.data_hora, e):
        data = data.astimezone(BRT) if data.tzinfo else data.replace(tzinfo=BRT)
        for _, achada, tem_hora, _ in datas_candidatas(linhas):
            mesmo_dia = achada.date() == data.date()
            if mesmo_dia and (
                not tem_hora or (achada.hour, achada.minute) == (data.hour, data.minute)
            ):
                e.data_hora = achada
                e.incertos.discard("data_hora")
                break

    for parte, alvo in (("pagador", e.pagador), ("favorecido", e.favorecido)):
        dados = sugestao.get(parte)
        if not isinstance(dados, dict):
            continue
        nome = dados.get("nome")
        campo_nome = f"{parte}.nome"
        if _ausente_ou_duvidoso(campo_nome, alvo.nome, e) and isinstance(nome, str):
            normal = val.normalizar_nome(nome)
            if len(normal) >= 5 and normal in normalizado:
                alvo.nome = re.sub(r"\s+", " ", nome).strip()
                e.incertos.discard(campo_nome)
        documento = dados.get("documento")
        campo_doc = f"{parte}.documento"
        if _ausente_ou_duvidoso(campo_doc, alvo.documento, e) and isinstance(documento, str):
            achado = achar_documento(documento)
            sinais = re.sub(r"[^0-9*•]", "", texto).replace("•", "*")
            if achado and achado[1] and re.sub(r"[^0-9*]", "", achado[0]) in sinais:
                alvo.documento = achado[0]
                e.incertos.discard(campo_doc)

    chave_pix = (sugestao.get("favorecido") or {}).get("chave_pix")
    if not e.favorecido.chave_pix and isinstance(chave_pix, str):
        if (
            len(val.normalizar_nome(chave_pix)) >= 5
            and val.normalizar_nome(chave_pix) in normalizado
        ):
            e.favorecido.chave_pix = chave_pix.strip()

    banco = sugestao.get("banco")
    if not e.banco and isinstance(banco, str) and val.normalizar_nome(banco) in normalizado:
        e.banco = banco.strip()

    identificador = sugestao.get("identificador")
    if not e.identificador and isinstance(identificador, str) and identificador in compacto:
        e.identificador = identificador.strip()


def _montar(e: Extracao, av: Avaliacao, texto: str, metodo: str, hash_arquivo: str):
    return DadosComprovante(
        valor=e.valor,
        data_hora=e.data_hora,
        id_transacao=e.id_transacao,
        pagador=e.pagador,
        favorecido=e.favorecido,
        banco=e.banco,
        identificador=e.identificador,
        confianca=av.confianca,
        campos_incertos=av.campos_incertos,
        requer_revisao_humana=av.requer_revisao,
        metodo=metodo,
        hash_arquivo=hash_arquivo,
        texto_bruto=texto,
    )


def processar_detalhado(
    conteudo: bytes,
    config: Configuracoes,
    *,
    usar_cache: bool = True,
    usar_llm: bool | None = None,
) -> Resultado:
    inicio = time.monotonic()
    hash_arquivo = calcular_hash(conteudo)
    cache = obter_cache(config) if usar_cache else None

    if cache and (guardado := cache.obter(hash_arquivo)):
        dados = guardado.model_copy(
            update={
                "metodo": "cache",
                "metodo_original": guardado.metodo_original or guardado.metodo,
            }
        )
        log.info(
            "comprovante hash=%s camada=cache original=%s", hash_arquivo[:12], dados.metodo_original
        )
        return Resultado(dados, 0, False, True)

    melhor = _ler(conteudo, config, escala=None)
    tentativas, pedida = 1, melhor.escala_pedida
    while (
        (melhor.avaliacao.requer_revisao or len(melhor.lido.texto) < TEXTO_MINIMO)
        and tentativas < MAX_LEITURAS
        and (proxima := PROXIMA_ESCALA.get(pedida))
    ):
        pedida = proxima
        candidata = _ler(conteudo, config, escala=proxima, superar=melhor.escala)
        if candidata is None:
            break
        tentativas += 1
        melhor = _melhor(melhor, candidata)

    texto = melhor.lido.texto
    if len(texto.strip()) < TEXTO_MINIMO:
        raise ErroApi(422, "Não foi possível ler texto na imagem.")

    extracao, avaliacao, metodo = melhor.extracao, melhor.avaliacao, "ocr_local"
    ligado = config.llm_fallback_ativo if usar_llm is None else usar_llm
    acionou_llm, tokens_entrada, tokens_saida = False, 0, 0
    if ligado and avaliacao.confianca < config.confianca_minima:
        resposta = llm.consultar(texto, config)
        if resposta:
            acionou_llm = True
            tokens_entrada, tokens_saida = resposta.tokens_entrada, resposta.tokens_saida
            _mesclar_llm(extracao, resposta.dados, texto)
            avaliacao = avaliar(extracao, melhor.lido.confianca_media, config.confianca_minima)
            metodo = "ocr_local+llm"
            log.info(
                "llm hash=%s tokens_entrada=%d tokens_saida=%d",
                hash_arquivo[:12], resposta.tokens_entrada, resposta.tokens_saida,
            )  # fmt: skip

    dados = _montar(extracao, avaliacao, texto, metodo, hash_arquivo)
    if cache:
        cache.salvar(dados)
    log.info(
        "comprovante hash=%s camada=%s tentativas=%d confianca=%.2f revisao=%s ms=%d",
        hash_arquivo[:12], metodo, tentativas, dados.confianca, dados.requer_revisao_humana,
        (time.monotonic() - inicio) * 1000,
    )  # fmt: skip
    return Resultado(dados, tentativas, acionou_llm, False, tokens_entrada, tokens_saida)


def processar(conteudo: bytes, config: Configuracoes) -> DadosComprovante:
    return processar_detalhado(conteudo, config).dados
