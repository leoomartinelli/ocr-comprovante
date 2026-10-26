import json
import sqlite3
import statistics
import time
from collections import Counter
from contextlib import closing
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

from app.config import Configuracoes
from app.parser import BRT

_SEGUNDOS_POR_DIA = 86400
MAX_BANCOS = 8
FAIXAS_CONFIANCA = 10

# campo do resultado → rótulo do painel
CAMPOS = {
    "valor": "Valor",
    "id_transacao": "ID da transação (E2E)",
    "data_hora": "Data e hora",
    "favorecido": "Nome do favorecido",
    "pagador": "Nome do pagador",
    "banco": "Banco",
    "identificador": "Identificador",
}


@dataclass
class Evento:
    """Uma chamada ao endpoint. Sem nomes, documentos, E2E nem texto: só o que o painel conta."""

    status: int
    duracao_ms: int
    motivo: str | None = None
    metodo: str | None = None
    metodo_original: str | None = None
    confianca: float | None = None
    revisao: bool | None = None
    valor: float | None = None
    banco: str | None = None
    encontrados: list[str] = field(default_factory=list)
    campos_incertos: list[str] = field(default_factory=list)
    tentativas: int = 0
    llm: bool = False
    tokens_entrada: int = 0
    tokens_saida: int = 0
    criado_em: float = field(default_factory=time.time)


class Metricas:
    """Histórico das chamadas em SQLite, para o painel. Apaga o que passou da retenção."""

    def __init__(self, caminho: str, retencao_dias: int):
        self._caminho = Path(caminho)
        self._retencao = retencao_dias * _SEGUNDOS_POR_DIA
        self._caminho.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._conectar()) as con, con:
            con.execute(
                "CREATE TABLE IF NOT EXISTS eventos ("
                "criado_em REAL NOT NULL, status INTEGER NOT NULL, dados TEXT NOT NULL)"
            )
            con.execute("CREATE INDEX IF NOT EXISTS eventos_criado_em ON eventos (criado_em)")
            con.execute("DELETE FROM eventos WHERE criado_em < ?", (time.time() - self._retencao,))

    def _conectar(self) -> sqlite3.Connection:
        con = sqlite3.connect(self._caminho, timeout=10)
        con.execute("PRAGMA journal_mode=WAL")
        return con

    def registrar(self, evento: Evento) -> None:
        with closing(self._conectar()) as con, con:
            con.execute(
                "INSERT INTO eventos (criado_em, status, dados) VALUES (?, ?, ?)",
                (evento.criado_em, evento.status, json.dumps(asdict(evento))),
            )

    def eventos(self, desde: float) -> list[Evento]:
        with closing(self._conectar()) as con:
            linhas = con.execute(
                "SELECT dados FROM eventos WHERE criado_em >= ? ORDER BY criado_em", (desde,)
            ).fetchall()
        return [Evento(**json.loads(linha[0])) for linha in linhas]

    def resumo(self, dias: int, agora: float | None = None) -> dict:
        agora = time.time() if agora is None else agora
        hoje = datetime.fromtimestamp(agora, BRT).date()
        primeiro_dia = hoje - timedelta(days=dias - 1)
        inicio = datetime.combine(primeiro_dia, datetime.min.time(), BRT).timestamp()
        return resumir(self.eventos(inicio), primeiro_dia, dias)


def _dia(evento: Evento) -> str:
    return datetime.fromtimestamp(evento.criado_em, BRT).date().isoformat()


def _percentil(valores: list[int], p: float) -> int | None:
    if not valores:
        return None
    ordenados = sorted(valores)
    return ordenados[min(len(ordenados) - 1, int(p * len(ordenados)))]


def resumir(eventos: list[Evento], primeiro_dia, dias: int) -> dict:
    sucessos = [e for e in eventos if e.status == 200]
    erros = [e for e in eventos if e.status != 200]
    revisao = [e for e in sucessos if e.revisao]

    por_dia = {
        (primeiro_dia + timedelta(days=i)).isoformat(): {"automatico": 0, "revisao": 0, "erro": 0}
        for i in range(dias)
    }
    for e in eventos:
        if (contagem := por_dia.get(_dia(e))) is not None:
            tipo = "erro" if e.status != 200 else "revisao" if e.revisao else "automatico"
            contagem[tipo] += 1

    faixas = [0] * FAIXAS_CONFIANCA
    for e in sucessos:
        faixas[min(FAIXAS_CONFIANCA - 1, int((e.confianca or 0) * FAIXAS_CONFIANCA))] += 1

    bancos = Counter(e.banco or "Não identificado" for e in sucessos).most_common()
    if len(bancos) > MAX_BANCOS:
        outros = sum(q for _, q in bancos[MAX_BANCOS - 1 :])
        bancos = bancos[: MAX_BANCOS - 1] + [("Outros", outros)]

    encontrados = Counter(c for e in sucessos for c in e.encontrados)
    incertos = Counter(c for e in sucessos for c in e.campos_incertos)
    motivos = Counter((e.status, e.motivo or "") for e in erros)
    metodos = Counter(e.metodo for e in sucessos)
    duracoes = [e.duracao_ms for e in eventos if e.metodo != "cache"]
    confiancas = [e.confianca for e in sucessos if e.confianca is not None]

    return {
        "periodo_dias": dias,
        "totais": {
            "recebidos": len(eventos),
            "sucesso": len(sucessos),
            "erro": len(erros),
            "automatico": len(sucessos) - len(revisao),
            "revisao": len(revisao),
            "cache": metodos["cache"],
            "llm": sum(e.llm for e in sucessos),
            "identificadores": encontrados["identificador"],
            "valor_total": round(sum(e.valor or 0 for e in sucessos if e.metodo != "cache"), 2),
            "confianca_media": round(statistics.mean(confiancas), 3) if confiancas else None,
            "duracao_mediana_ms": int(statistics.median(duracoes)) if duracoes else None,
            "duracao_p95_ms": _percentil(duracoes, 0.95),
            "tokens_entrada": sum(e.tokens_entrada for e in eventos),
            "tokens_saida": sum(e.tokens_saida for e in eventos),
        },
        "por_dia": [{"dia": dia, **contagem} for dia, contagem in por_dia.items()],
        "campos": [
            {"campo": c, "rotulo": r, "encontrados": encontrados[c]} for c, r in CAMPOS.items()
        ],
        "incertos": [{"campo": c, "quantidade": q} for c, q in incertos.most_common()],
        "erros": [
            {"status": s, "motivo": m, "quantidade": q} for (s, m), q in motivos.most_common()
        ],
        "metodos": [{"metodo": m, "quantidade": q} for m, q in metodos.most_common()],
        "bancos": [{"banco": b, "quantidade": q} for b, q in bancos],
        "confianca": [
            {"de": i / FAIXAS_CONFIANCA, "ate": (i + 1) / FAIXAS_CONFIANCA, "quantidade": q}
            for i, q in enumerate(faixas)
        ],
        "ultimos": [
            {
                "quando": datetime.fromtimestamp(e.criado_em, BRT).isoformat(timespec="seconds"),
                "status": e.status,
                "motivo": e.motivo,
                "metodo": e.metodo,
                "confianca": e.confianca,
                "revisao": e.revisao,
                "identificador": "identificador" in e.encontrados,
                "banco": e.banco,
                "duracao_ms": e.duracao_ms,
            }
            for e in reversed(eventos[-25:])
        ],
    }


_metricas: Metricas | None = None


def obter_metricas(config: Configuracoes) -> Metricas | None:
    global _metricas
    if not config.metricas_ativo:
        return None
    if _metricas is None:
        _metricas = Metricas(config.metricas_db_path, config.metricas_retencao_dias)
    return _metricas


def definir_metricas(metricas: Metricas | None) -> None:
    global _metricas
    _metricas = metricas
