import numpy as np

from app.leitura import ler_imagem
from app.motor_ocr import CaixaTexto
from app.preprocessamento import Faixa, ImagemPreparada


class MotorPorFaixa:
    """Devolve caixas pré-definidas, uma lista por chamada (uma por faixa)."""

    def __init__(self, *respostas: list[CaixaTexto]):
        self._respostas = list(respostas)

    def ler(self, imagem):
        return self._respostas.pop(0)


def _faixas(*deslocamentos, altura=2000):
    ultimo = len(deslocamentos) - 1
    return ImagemPreparada(
        faixas=[
            Faixa(np.zeros((altura, 100, 3), np.uint8), d, i == 0, i == ultimo)
            for i, d in enumerate(deslocamentos)
        ],
        escala=1.0,
        escala_pedida=1.0,
        modo_escuro=False,
    )


def caixa(texto, x, topo, base=None, confianca=0.99):
    return CaixaTexto(texto, x, topo, base if base is not None else topo + 30, confianca)


def test_rotulo_e_valor_na_mesma_altura_viram_uma_linha_ordenada_por_x():
    motor = MotorPorFaixa([caixa("R$ 100,00", 300, 100), caixa("Valor", 20, 102)])
    lido = ler_imagem(motor, _faixas(0))
    assert lido.texto == "Valor R$ 100,00"


def test_linhas_diferentes_ficam_em_ordem_vertical():
    motor = MotorPorFaixa([caixa("segunda", 0, 200), caixa("primeira", 0, 100)])
    assert ler_imagem(motor, _faixas(0)).texto == "primeira\nsegunda"


def test_sobreposicao_entre_faixas_nao_duplica_linhas():
    # Linha "Nome: X" cai na sobreposição (globais 1850–1880) e aparece nas duas faixas.
    faixa1 = [caixa("Topo", 0, 50), caixa("Nome: X", 0, 1850)]
    faixa2 = [caixa("Nome: X", 0, 50), caixa("Fim", 0, 400)]  # y global = 1800 + 50
    lido = ler_imagem(MotorPorFaixa(faixa1, faixa2), _faixas(0, 1800))
    assert lido.texto.split("\n") == ["Topo", "Nome: X", "Fim"]


def test_caixa_cortada_na_emenda_e_descartada():
    # "Nome: Xab" foi cortada no fim da faixa 1 e reaparece inteira na faixa 2.
    faixa1 = [caixa("Topo", 0, 50), caixa("Nome: X", 0, 1975, base=1998)]
    faixa2 = [caixa("Nome: XYZ", 0, 170), caixa("Fim", 0, 400)]
    lido = ler_imagem(MotorPorFaixa(faixa1, faixa2), _faixas(0, 1800))
    assert "Nome: X\n" not in lido.texto + "\n" and "Nome: XYZ" in lido.texto


def test_caixa_colada_no_topo_da_faixa_seguinte_e_descartada():
    faixa1 = [caixa("Topo", 0, 50)]
    faixa2 = [caixa("meia linha", 0, 3, base=25), caixa("Fim", 0, 400)]
    assert "meia" not in ler_imagem(MotorPorFaixa(faixa1, faixa2), _faixas(0, 1800)).texto


def test_confianca_media_e_ponderada_pelo_tamanho_do_texto():
    motor = MotorPorFaixa(
        [caixa("aaaaaaaaaa", 0, 0, confianca=1.0), caixa("b", 0, 100, confianca=0.0)]
    )
    assert ler_imagem(motor, _faixas(0)).confianca_media == round(10 / 11, 3)


def test_sem_texto_devolve_vazio():
    lido = ler_imagem(MotorPorFaixa([]), _faixas(0))
    assert lido.texto == "" and lido.confianca_media == 0.0
