from dataclasses import dataclass, replace
from statistics import mean

from app.motor_ocr import CaixaTexto, MotorOCR
from app.preprocessamento import ImagemPreparada
from app.validadores import normalizar_nome

MARGEM_CORTE = 10
TOLERANCIA_DUPLICATA = 0.6


@dataclass(frozen=True)
class TextoLido:
    texto: str
    confianca_media: float


def _centro(c: CaixaTexto) -> float:
    return (c.topo + c.base) / 2


def _caixas_das_faixas(motor: MotorOCR, preparada: ImagemPreparada) -> list[CaixaTexto]:
    """Lê cada faixa e devolve as caixas em coordenadas globais, descartando as que foram
    cortadas na emenda entre faixas (elas aparecem inteiras na faixa vizinha)."""
    todas: list[CaixaTexto] = []
    for faixa in preparada.faixas:
        altura = faixa.imagem.shape[0]
        for c in motor.ler(faixa.imagem):
            if not faixa.primeira and c.topo < MARGEM_CORTE:
                continue
            if not faixa.ultima and c.base > altura - MARGEM_CORTE:
                continue
            todas.append(
                replace(
                    c,
                    topo=c.topo + faixa.deslocamento_y,
                    base=c.base + faixa.deslocamento_y,
                )
            )
    return todas


def _sem_duplicatas(caixas: list[CaixaTexto]) -> list[CaixaTexto]:
    mantidas: list[CaixaTexto] = []
    for c in sorted(caixas, key=_centro):
        chave = normalizar_nome(c.texto)
        tolerancia = TOLERANCIA_DUPLICATA * (c.base - c.topo)
        duplicada = any(
            normalizar_nome(m.texto) == chave and abs(_centro(m) - _centro(c)) < tolerancia
            for m in mantidas[-6:]
        )
        if not duplicada:
            mantidas.append(c)
    return mantidas


def _agrupar_em_linhas(caixas: list[CaixaTexto]) -> list[str]:
    """Junta caixas na mesma altura ("Valor" à esquerda, "R$ 100,00" à direita)."""
    grupos: list[list[CaixaTexto]] = []
    for c in sorted(caixas, key=_centro):
        if grupos:
            grupo = grupos[-1]
            centro = mean(_centro(x) for x in grupo)
            altura = min(c.base - c.topo, mean(x.base - x.topo for x in grupo))
            if abs(_centro(c) - centro) < 0.5 * altura:
                grupo.append(c)
                continue
        grupos.append([c])
    return [" ".join(x.texto for x in sorted(g, key=lambda c: c.x)) for g in grupos]


def ler_imagem(motor: MotorOCR, preparada: ImagemPreparada) -> TextoLido:
    caixas = _sem_duplicatas(_caixas_das_faixas(motor, preparada))
    if not caixas:
        return TextoLido("", 0.0)
    peso_total = sum(len(c.texto) for c in caixas) or 1
    confianca = sum(c.confianca * len(c.texto) for c in caixas) / peso_total
    return TextoLido("\n".join(_agrupar_em_linhas(caixas)), round(confianca, 3))
