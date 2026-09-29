from datetime import datetime

from app.parser import BRT, Extracao
from app.pontuacao import avaliar
from app.schemas import Favorecido, Pagador
from app.validadores import data_do_e2e
from tests.textos import E2E


def _extracao(**campos) -> Extracao:
    base = dict(
        valor=100.0,
        data_hora=datetime(2026, 9, 28, 22, 29, 14, tzinfo=BRT),
        id_transacao=E2E,
        e2e_utc=data_do_e2e(E2E),
        favorecido=Favorecido(nome="ESCOLA"),
    )
    return Extracao(**{**base, **campos})


def test_valor_e2e_e_data_coerente_dao_confianca_alta_sem_llm():
    av = avaliar(_extracao(), 0.97, 0.75)
    assert av.confianca >= 0.75 and not av.requer_revisao and av.campos_incertos == []


def test_tudo_preenchido_chega_a_um():
    e = _extracao(
        favorecido=Favorecido(nome="X", documento="***.1"),
        pagador=Pagador(nome="Y", documento="***.2"),
    )
    assert avaliar(e, 0.99, 0.75).confianca == 1.0


def test_sem_nome_do_favorecido_pede_revisao_mesmo_com_confianca_boa():
    av = avaliar(_extracao(favorecido=Favorecido()), 0.97, 0.75)
    assert av.confianca == 0.75 and av.requer_revisao
    assert "favorecido.nome" in av.campos_incertos


def test_data_incoerente_com_o_e2e_marca_os_dois_como_incertos():
    e = _extracao(data_hora=datetime(2026, 9, 20, 22, 29, tzinfo=BRT))  # E2E é de 28-29/09
    av = avaliar(e, 0.97, 0.75)
    assert {"data_hora", "id_transacao"} <= set(av.campos_incertos)
    assert av.confianca < 0.75 and av.requer_revisao


def test_ocr_inseguro_derruba_a_confianca():
    assert avaliar(_extracao(), 0.70, 0.75).confianca < avaliar(_extracao(), 0.95, 0.75).confianca
    assert avaliar(_extracao(), 0.50, 0.75).confianca < avaliar(_extracao(), 0.70, 0.75).confianca


def test_campo_duvidoso_nao_soma_peso():
    e = _extracao()
    e.incertos.add("valor")
    av = avaliar(e, 0.97, 0.75)
    assert av.confianca == 0.55 and av.requer_revisao


def test_nada_extraido_da_zero():
    av = avaliar(Extracao(), 0.9, 0.75)
    assert av.confianca == 0.0 and av.requer_revisao
    assert set(av.campos_incertos) == {"valor", "id_transacao", "data_hora", "favorecido.nome"}
