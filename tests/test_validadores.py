import pytest

from app import validadores as v
from tests.textos import E2E


@pytest.mark.parametrize("cpf", ["52998224725", "11144477735"])
def test_cpf_valido(cpf):
    assert v.validar_cpf(cpf)


@pytest.mark.parametrize("cpf", ["52998224726", "11111111111", "1234", "abcdefghijk", ""])
def test_cpf_invalido(cpf):
    assert not v.validar_cpf(cpf)


def test_cnpj():
    assert v.validar_cnpj("11222333000181")
    assert not v.validar_cnpj("11222333000182")
    assert not v.validar_cnpj("00000000000000")


def test_e2e_valido_devolve_data_utc():
    data = v.data_do_e2e(E2E)
    assert (data.year, data.month, data.day, data.hour, data.minute) == (2026, 9, 29, 1, 29)
    assert data.utcoffset().total_seconds() == 0


@pytest.mark.parametrize(
    "e2e",
    [
        E2E[:-1],  # 31 caracteres
        E2E + "x",  # 33
        "F" + E2E[1:],  # não começa com E
        "E12345678202613290129Ab3dE9fGh1J",  # mês 13
        "E12345678201901010129Ab3dE9fGh1J",  # antes do Pix existir
        "E1234567820260929012!Ab3dE9fGh1J"[:32],  # símbolo
    ],
)
def test_e2e_invalido(e2e):
    assert v.data_do_e2e(e2e) is None


def test_corrigir_e2e_troca_letras_por_digitos_so_na_parte_numerica():
    lido = "E1234567B2O2609290I29Ab3dE9fGh1J"  # B→8, O→0, I→1
    assert v.corrigir_e2e(lido) == E2E


def test_corrigir_e2e_nao_mexe_na_cauda():
    assert v.corrigir_e2e(E2E) == E2E
    assert v.corrigir_e2e(E2E[:21] + "O0lI1Ss8Bxy") == E2E[:21] + "O0lI1Ss8Bxy"


def test_corrigir_e2e_aceita_e_minusculo_e_recusa_o_que_nao_da():
    assert v.corrigir_e2e("e" + E2E[1:]) == E2E
    assert v.corrigir_e2e("E1234567820260929XXXXAb3dE9fGh1J") is None
    assert v.corrigir_e2e("curto") is None


def test_normalizar_nome_ignora_espaco_acento_e_caixa():
    assert v.normalizar_nome("José da  Silva") == v.normalizar_nome("JOSEDASILVA")
