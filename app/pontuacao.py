from dataclasses import dataclass
from datetime import timedelta

from app.parser import Extracao

PESOS = {
    "valor": 0.30,
    "id_transacao": 0.30,
    "data_hora": 0.15,
    "favorecido.nome": 0.10,
    "favorecido.documento": 0.05,  # vale também a chave Pix
    "pagador.nome": 0.05,
    "pagador.documento": 0.05,
}
CRITICOS = frozenset({"valor", "id_transacao", "data_hora", "favorecido.nome"})
TOLERANCIA_E2E = timedelta(days=1)
PENALIDADE_OCR_FRACO = ((0.65, 0.20), (0.80, 0.10))


@dataclass(frozen=True)
class Avaliacao:
    confianca: float
    campos_incertos: list[str]
    requer_revisao: bool


def avaliar(e: Extracao, confianca_ocr: float, limiar: float) -> Avaliacao:
    """Confiança = soma dos pesos dos campos presentes e sem dúvida, menos penalidade se o
    OCR estiver inseguro. valor + E2E válido + data coerente = 0,75."""
    incertos = set(e.incertos)
    if e.e2e_utc and e.data_hora and abs(e.e2e_utc - e.data_hora) > TOLERANCIA_E2E:
        incertos |= {"data_hora", "id_transacao"}

    presentes = {
        "valor": e.valor is not None,
        "id_transacao": e.id_transacao is not None,
        "data_hora": e.data_hora is not None,
        "favorecido.nome": e.favorecido.nome is not None,
        "favorecido.documento": bool(e.favorecido.documento or e.favorecido.chave_pix),
        "pagador.nome": e.pagador.nome is not None,
        "pagador.documento": e.pagador.documento is not None,
    }
    soma = sum(p for campo, p in PESOS.items() if presentes[campo] and campo not in incertos)
    for limite, penalidade in PENALIDADE_OCR_FRACO:
        if confianca_ocr < limite:
            soma -= penalidade
            break

    ausentes_criticos = {c for c in CRITICOS if not presentes[c]}
    campos_incertos = sorted(incertos | ausentes_criticos)
    confianca = round(min(1.0, max(0.0, soma)), 2)
    return Avaliacao(
        confianca=confianca,
        campos_incertos=campos_incertos,
        requer_revisao=confianca < limiar or bool(CRITICOS & set(campos_incertos)),
    )
