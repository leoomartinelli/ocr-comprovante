from datetime import datetime

import pytest

from app.parser import BRT, interpretar
from tests.textos import E2E, ESTILO_A_DEGRADADO, ESTILO_A_LIMPO, ESTILO_B


def test_estilo_a_limpo_extrai_tudo():
    e = interpretar(ESTILO_A_LIMPO)
    assert e.valor == 100.0
    assert e.data_hora == datetime(2026, 9, 28, 22, 29, 14, tzinfo=BRT)  # débito, não o cabeçalho
    assert e.id_transacao == E2E
    assert e.favorecido.nome == "CARLOS MENDES DE ALBUQUERQUE"
    assert e.favorecido.documento == "***.123.456-**"
    assert e.pagador.nome == "MARIA LUISA DA SILVA"
    assert e.pagador.documento == "***.654.321-**"
    assert e.banco == "BCO EXEMPLO S.A."
    assert e.identificador == "COIN999"
    assert e.incertos == set()


def test_ocr_degradado_tolera_espacos_perdidos_e_rotulos_trocados():
    e = interpretar(ESTILO_A_DEGRADADO)
    assert e.valor == 100.0
    assert e.id_transacao == E2E  # "Numer0 de controle" não atrapalha
    assert e.identificador == "COIN999"  # "ldentificador" / "COlN999"
    assert e.data_hora == datetime(2026, 9, 28, 22, 29, 14, tzinfo=BRT)
    assert e.favorecido.nome == "CARLOSMENDESDEALBUQUERQUE"
    assert "favorecido.nome" in e.incertos  # nome colado é sinalizado, não escondido


def test_estilo_b_rotulo_em_linha_propria_e_mes_por_extenso():
    e = interpretar(ESTILO_B)
    assert e.valor == 1500.0
    assert e.data_hora == datetime(2026, 9, 28, 22, 29, 14, tzinfo=BRT)
    assert e.favorecido.nome == "CARLOS MENDES DE ALBUQUERQUE"
    assert e.favorecido.chave_pix == "carlos@exemplo.com.br"
    assert e.banco == "PAGAMENTOS EXEMPLO S.A."
    assert e.pagador.nome == "MARIA LUISA DA SILVA"
    assert e.id_transacao == E2E


def test_texto_sem_dados_nao_inventa_nada():
    e = interpretar("Bom dia, segue o comprovante")
    assert (e.valor, e.data_hora, e.id_transacao, e.banco, e.identificador) == (None,) * 5
    assert e.favorecido.nome is None and e.pagador.nome is None


class TestE2E:
    def test_quebrado_em_duas_linhas_com_letras_no_lugar_de_digitos(self):
        texto = "ID da transacao\nE1234567B2O2609290129\nAb3dE9fGh1J"
        assert interpretar(texto).id_transacao == E2E

    def test_partido_por_espaco_na_mesma_linha(self):
        texto = f"Controle: {E2E[:24]} {E2E[24:]}"
        assert interpretar(texto).id_transacao == E2E

    def test_rotulo_colado_no_inicio(self):
        assert interpretar(f"controle{E2E}").id_transacao == E2E

    def test_cortado_nao_e_completado_com_palavras_vizinhas(self):
        # Regressão: "E2E cortado na borda" + "Dados" formava 32 caracteres válidos.
        cortado = E2E[:27]
        texto = f"Numero de controle: {cortado}\nDados de quem pagou"
        assert interpretar(texto).id_transacao is None
        assert interpretar(f"Numero: {cortado} Dados").id_transacao is None

    def test_com_data_impossivel_e_recusado(self):
        assert interpretar("E12345678202613290129Ab3dE9fGh1J").id_transacao is None


class TestValor:
    def test_milhar_e_centavos(self):
        assert interpretar("Valor R$ 1.234,56").valor == 1234.56

    def test_ocr_leu_rs_e_ponto_decimal(self):
        assert interpretar("Valor: RS 45.75").valor == 45.75

    def test_divergencia_entre_destaque_e_corpo_e_sinalizada(self):
        e = interpretar("Valor: R$ 100,00\nValor: R$ 190,00")
        assert e.valor == 100.0 and "valor" in e.incertos

    def test_valores_iguais_confirmam(self):
        assert "valor" not in interpretar("R$ 100,00\nValor: R$ 100,00").incertos

    def test_tarifa_saldo_e_desconto_nao_entram(self):
        e = interpretar("Valor: R$ 100,00\nTarifa: R$ 2,50\nSaldo: R$ 900,00\nDesconto R$ 5,00")
        assert e.valor == 100.0 and "valor" not in e.incertos

    def test_valor_zero_e_ignorado(self):
        assert interpretar("Tarifa isenta\nR$ 0,00").valor is None


class TestData:
    def test_prefere_a_data_do_debito_ao_cabecalho(self):
        e = interpretar("28/09/2026 - 22:29:17\nData do débito: 28/09/2026 - 22:29:14")
        assert e.data_hora.second == 14

    def test_sem_hora_marca_incerto(self):
        e = interpretar("Data: 28/09/2026")
        assert e.data_hora == datetime(2026, 9, 28, tzinfo=BRT) and "data_hora" in e.incertos

    def test_ignora_vencimento_e_agendamento(self):
        assert interpretar("Vencimento: 10/10/2026\nAgendamento: 11/10/2026").data_hora is None

    @pytest.mark.parametrize(
        "texto",
        ["28 de setembro de 2026 às 22:29", "28 SET 2026 22:29", "28/09/2026 às 22:29:00"],
    )
    def test_formatos(self, texto):
        assert interpretar(texto).data_hora == datetime(2026, 9, 28, 22, 29, tzinfo=BRT)

    def test_data_impossivel_e_futura_sao_recusadas(self):
        assert interpretar("31/02/2026 10:00").data_hora is None
        assert interpretar("01/01/2099 10:00").data_hora is None


class TestDocumentos:
    def test_cpf_completo_valido(self):
        e = interpretar("Destino\nNome: FULANO DE TAL\nCPF: 529.982.247-25")
        assert e.favorecido.documento == "529.982.247-25" and not e.incertos

    def test_cpf_completo_com_digito_errado_e_sinalizado(self):
        e = interpretar("Destino\nNome: FULANO DE TAL\nCPF: 529.982.247-26")
        assert e.favorecido.documento == "529.982.247-26"
        assert "favorecido.documento" in e.incertos

    def test_cnpj_completo_valido(self):
        e = interpretar("Destino\nNome: ESCOLA LTDA\nCNPJ: 11.222.333/0001-81")
        assert e.favorecido.documento == "11.222.333/0001-81" and not e.incertos

    @pytest.mark.parametrize("mascarado", ["***.123.456-**", "•••.123.456-••", "***123456**"])
    def test_mascarado_e_aceito_sem_validar(self, mascarado):
        e = interpretar(f"Destino\nCPF: {mascarado}")
        assert e.favorecido.documento == "***.123.456-**" and not e.incertos

    def test_documento_so_conta_dentro_da_secao_da_parte(self):
        e = interpretar("Dados de quem pagou\nNome: A B C\nCPF: ***.654.321-**")
        assert e.favorecido.documento is None and e.pagador.documento == "***.654.321-**"


class TestPartes:
    def test_nome_com_rotulo_explicito_sem_secao(self):
        e = interpretar("Nome do recebedor: ESCOLA EXEMPLO\nNome do pagador: JOSE PEREIRA")
        assert e.favorecido.nome == "ESCOLA EXEMPLO" and e.pagador.nome == "JOSE PEREIRA"

    def test_secao_neutra_nao_vaza_para_a_parte_anterior(self):
        texto = (
            "Dados de quem recebeu\nNome: ESCOLA EXEMPLO\nDados do pagamento\nNome: NAO DEVE ENTRAR"
        )
        assert interpretar(texto).favorecido.nome == "ESCOLA EXEMPLO"

    def test_banco_do_favorecido_tem_preferencia(self):
        texto = "Origem\nInstituição: BANCO A\nDestino\nInstituição: BANCO B"
        assert interpretar(texto).banco == "BANCO B"

    @pytest.mark.parametrize(
        ("nome", "sinalizado"),
        [
            ("CARLOSMENDES DE ALBUQUERQUE", True),  # espaço perdido no meio
            ("FERNANDOALBUQUERQUEDIAS", True),
            ("MARIA LUISA DA SILVA", False),
            ("VASCONCELOS DE ALBUQUERQUE", False),  # palavras longas mas legítimas
        ],
    )
    def test_nome_com_espaco_perdido_e_sinalizado(self, nome, sinalizado):
        e = interpretar(f"Destino\nNome: {nome}")
        assert ("favorecido.nome" in e.incertos) is sinalizado

    @pytest.mark.parametrize("rotulo", ["Instituição", "Instituicao", "Instituio", "Institui��o"])
    def test_rotulo_instituicao_tolera_acentos_perdidos(self, rotulo):
        assert interpretar(f"Destino\n{rotulo}: BCO EXEMPLO S.A.").banco == "BCO EXEMPLO S.A."

    def test_sem_secoes_nao_atribui_nome_a_ninguem(self):
        e = interpretar("Nome: FULANO DE TAL")
        assert e.favorecido.nome is None and e.pagador.nome is None


class TestIdentificador:
    @pytest.mark.parametrize(
        ("lido", "esperado"),
        [
            ("COIN999", "COIN999"),
            ("COlN999", "COIN999"),
            ("COiN999", "COIN999"),
            ("coin123", "coin123"),
        ],
    )
    def test_corrige_i_e_l_minusculos_so_em_identificador_maiusculo(self, lido, esperado):
        assert interpretar(f"Identificador: {lido}").identificador == esperado

    def test_identificador_misto_nao_e_alterado(self):
        assert interpretar("Identificador: Pedido123").identificador == "Pedido123"
