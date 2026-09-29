"""Pipeline completo com o RapidOCR de verdade, em prints sintéticos difíceis (~30 s).

Rode só estes com `uv run pytest -m ocr`; pule com `uv run pytest -m "not ocr"`.
"""

from datetime import datetime

import pytest
from PIL import Image

from app.config import Configuracoes
from app.motor_ocr import definir_motor
from app.servico import processar_detalhado
from app.validadores import normalizar_nome
from scripts.sintetico import (
    BRT,
    caminho_fonte,
    como_bytes,
    gerar_e2e,
    linhas_estilo_a,
    linhas_estilo_b,
    renderizar,
)

pytestmark = [
    pytest.mark.ocr,
    pytest.mark.skipif(caminho_fonte() is None, reason="sem fonte TrueType para gerar o print"),
]

MOMENTO = datetime(2026, 9, 28, 22, 29, 14, tzinfo=BRT)
E2E = gerar_e2e(MOMENTO, semente=7)
FAVORECIDO = "CARLOS MENDES DE ALBUQUERQUE"


@pytest.fixture(autouse=True)
def _motor_real():
    definir_motor(None)  # força o RapidOCR real


def _ler(img: Image.Image, formato="PNG", **opcoes):
    config = Configuracoes(api_key="x", cache_ativo=False)
    return processar_detalhado(como_bytes(img, formato, **opcoes), config).dados


def _estilo_a(**opcoes):
    linhas = linhas_estilo_a(100.0, MOMENTO, E2E, FAVORECIDO, "MARIA LUISA DA SILVA", "COIN999")
    return renderizar(linhas, **opcoes)


def _confere(dados, *, nome=True):
    assert dados.valor == 100.0
    assert dados.id_transacao == E2E
    assert dados.data_hora.date() == MOMENTO.date() and dados.data_hora.hour == 22
    assert dados.identificador == "COIN999"
    if nome:
        assert dados.favorecido.nome == FAVORECIDO


def test_print_claro_720():
    dados = _ler(_estilo_a(largura=720))
    _confere(dados)
    assert not dados.requer_revisao_humana and dados.confianca >= 0.75


def test_print_em_modo_escuro_jpeg_recomprimido():
    dados = _ler(_estilo_a(largura=720, escuro=True), "JPEG", quality=35)
    _confere(dados)
    assert not dados.requer_revisao_humana


def test_print_estreito_reduzido_como_o_whatsapp_faz():
    base = _estilo_a(largura=720)
    estreito = base.resize((540, round(base.height * 540 / 720)), Image.LANCZOS)
    dados = _ler(estreito, "JPEG", quality=50)
    # campos críticos corretos; o nome pode sair colado, mas então vem sinalizado
    assert dados.valor == 100.0 and dados.id_transacao == E2E
    if dados.favorecido.nome != FAVORECIDO:
        assert "favorecido.nome" in dados.campos_incertos and dados.requer_revisao_humana


def test_print_comprido_e_dividido_em_faixas_sem_duplicar_nem_perder_campos():
    dados = _ler(_estilo_a(largura=720, espaco=150))  # ~3000 px de altura
    _confere(dados)
    assert normalizar_nome(dados.texto_bruto).count("QUEMRECEBEU") == 1  # sem linhas duplicadas


def test_layout_com_rotulo_em_linha_propria():
    linhas = linhas_estilo_b(1500.0, MOMENTO, E2E, FAVORECIDO, "MARIA LUISA DA SILVA", "COIN999")
    dados = _ler(renderizar(linhas, largura=1080))
    assert dados.valor == 1500.0 and dados.id_transacao == E2E
    assert dados.favorecido.nome == FAVORECIDO
