import os
from collections.abc import Callable

os.environ["API_KEY"] = "chave-de-teste"
os.environ["CACHE_ATIVO"] = "false"

import numpy as np
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.cache import definir_cache
from app.main import app
from app.motor_ocr import CaixaTexto, definir_motor
from scripts.sintetico import como_bytes
from tests.textos import ESTILO_A_LIMPO

CHAVE = {"X-API-Key": "chave-de-teste"}


class MotorFalso:
    """Devolve o texto dado (uma caixa por linha), ignorando a imagem."""

    def __init__(self, texto: str | Callable[[np.ndarray], str]):
        self.texto = texto
        self.chamadas: list[tuple[int, int]] = []

    def ler(self, imagem: np.ndarray) -> list[CaixaTexto]:
        self.chamadas.append((imagem.shape[1], imagem.shape[0]))
        texto = self.texto(imagem) if callable(self.texto) else self.texto
        return [
            CaixaTexto(linha, 0, i * 50, i * 50 + 30, 0.98)
            for i, linha in enumerate(texto.splitlines())
        ]


@pytest.fixture(autouse=True)
def _isolar_singletons():
    yield
    definir_motor(None)
    definir_cache(None)


@pytest.fixture
def motor_falso() -> MotorFalso:
    motor = MotorFalso(ESTILO_A_LIMPO)
    definir_motor(motor)
    return motor


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture
def png() -> bytes:
    return como_bytes(Image.new("RGB", (900, 600), "white"))
