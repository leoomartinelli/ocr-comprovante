import threading
from dataclasses import dataclass
from typing import Protocol

import numpy as np


@dataclass(frozen=True)
class CaixaTexto:
    texto: str
    x: float
    topo: float
    base: float
    confianca: float


class MotorOCR(Protocol):
    """Interface do motor de OCR. Para trocar (ex.: Tesseract), implemente `ler`."""

    def ler(self, imagem: np.ndarray) -> list[CaixaTexto]: ...


class MotorRapidOCR:
    def __init__(self) -> None:
        from rapidocr_onnxruntime import RapidOCR

        self._ocr = RapidOCR()

    def ler(self, imagem: np.ndarray) -> list[CaixaTexto]:
        resultado, _ = self._ocr(imagem, use_cls=False)
        caixas = []
        for pontos, texto, confianca in resultado or []:
            xs = [p[0] for p in pontos]
            ys = [p[1] for p in pontos]
            caixas.append(CaixaTexto(texto, min(xs), min(ys), max(ys), float(confianca)))
        return caixas


_motor: MotorOCR | None = None
_trava = threading.Lock()


def obter_motor() -> MotorOCR:
    global _motor
    with _trava:
        if _motor is None:
            _motor = MotorRapidOCR()
        return _motor


def definir_motor(motor: MotorOCR | None) -> None:
    """Troca o motor (testes ou outra implementação)."""
    global _motor
    with _trava:
        _motor = motor
