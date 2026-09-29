from datetime import datetime
from types import SimpleNamespace

import anthropic
import httpx2 as httpx
import pytest

from app import llm
from app.config import Configuracoes
from app.parser import BRT, Extracao, interpretar
from app.schemas import Favorecido
from app.servico import _mesclar_llm
from tests.textos import E2E


def _config(**campos) -> Configuracoes:
    return Configuracoes(api_key="x", anthropic_api_key="chave-falsa", **campos)


class ClienteFalso:
    def __init__(self, texto: str = "{}", erro: Exception | None = None):
        self.texto, self.erro, self.chamadas = texto, erro, []
        self.messages = self

    def create(self, **kwargs):
        self.chamadas.append(kwargs)
        if self.erro:
            raise self.erro
        return SimpleNamespace(
            content=[SimpleNamespace(type="text", text=self.texto)],
            usage=SimpleNamespace(input_tokens=120, output_tokens=60),
        )


@pytest.fixture
def cliente(monkeypatch):
    def usar(**kwargs) -> ClienteFalso:
        falso = ClienteFalso(**kwargs)
        monkeypatch.setattr(llm, "_obter_cliente", lambda config: falso)
        return falso

    return usar


class TestConsultar:
    def test_devolve_json_e_tokens(self, cliente):
        falso = cliente(texto='Claro! {"valor": 100.0, "banco": null} fim')
        resposta = llm.consultar("texto do ocr", _config())
        assert resposta.dados == {"valor": 100.0, "banco": None}
        assert (resposta.tokens_entrada, resposta.tokens_saida) == (120, 60)
        chamada = falso.chamadas[0]
        assert chamada["model"] == "claude-haiku-4-5" and chamada["temperature"] == 0
        assert chamada["messages"] == [{"role": "user", "content": "texto do ocr"}]

    def test_limita_o_tamanho_da_entrada(self, cliente):
        falso = cliente(texto="{}")
        llm.consultar("x" * 10_000, _config(llm_max_chars=500))
        assert len(falso.chamadas[0]["messages"][0]["content"]) == 500

    def test_sem_chave_nao_chama_ninguem(self, cliente):
        falso = cliente()
        assert llm.consultar("texto", Configuracoes(api_key="x")) is None
        assert falso.chamadas == []

    @pytest.mark.parametrize("resposta", ["nada de json aqui", "{quebrado", "[1, 2]", ""])
    def test_resposta_que_nao_e_objeto_json_vira_none(self, cliente, resposta):
        cliente(texto=resposta)
        assert llm.consultar("texto", _config()) is None

    def test_erro_de_conexao_vira_none(self, cliente):
        pedido = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
        cliente(erro=anthropic.APIConnectionError(request=pedido))
        assert llm.consultar("texto", _config()) is None

    def test_erro_http_vira_none(self, cliente):
        pedido = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
        resposta = httpx.Response(529, request=pedido)
        cliente(erro=anthropic.APIStatusError("sobrecarga", response=resposta, body=None))
        assert llm.consultar("texto", _config()) is None


def _extracao(texto: str) -> Extracao:
    return interpretar(texto)


class TestMesclar:
    def test_separa_nome_colado_sem_inventar_letras(self):
        texto = "Destino\nNome\nCARLOSMENDESDEALBUQUERQUE"
        e = _extracao(texto)
        assert "favorecido.nome" in e.incertos
        _mesclar_llm(e, {"favorecido": {"nome": "CARLOS MENDES DE ALBUQUERQUE"}}, texto)
        assert e.favorecido.nome == "CARLOS MENDES DE ALBUQUERQUE"
        assert "favorecido.nome" not in e.incertos

    def test_recusa_nome_que_nao_esta_no_texto(self):
        texto = "Destino\nNome\nCARLOSMENDESDEALBUQUERQUE"
        e = _extracao(texto)
        _mesclar_llm(e, {"favorecido": {"nome": "FULANO INVENTADO DA SILVA"}}, texto)
        assert e.favorecido.nome == "CARLOSMENDESDEALBUQUERQUE"

    def test_recupera_e2e_partido_que_o_parser_recusou(self):
        partido = f"{E2E[:21]} {E2E[21:]}".replace("Ab3dE9fGh1J", "AbcdEfGhIjK")
        real = partido.replace(" ", "")
        texto = f"Valor R$ 10,00\nControle {partido}"
        e = _extracao(texto)
        assert e.id_transacao is None
        _mesclar_llm(e, {"id_transacao": real}, texto)
        assert e.id_transacao == real and e.e2e_utc is not None

    def test_recusa_e2e_que_nao_esta_no_texto(self):
        texto = "Valor R$ 10,00\nControle E1234567820260929"
        e = _extracao(texto)
        _mesclar_llm(e, {"id_transacao": E2E}, texto)
        assert e.id_transacao is None

    def test_recusa_valor_que_nao_esta_no_texto(self):
        e = _extracao("Sem valor legivel")
        _mesclar_llm(e, {"valor": 999.99}, "Sem valor legivel")
        assert e.valor is None

    def test_resolve_valor_divergente_com_um_dos_valores_do_texto(self):
        texto = "Valor: R$ 100,00\nValor: R$ 190,00"
        e = _extracao(texto)
        _mesclar_llm(e, {"valor": 190.0}, texto)
        assert e.valor == 190.0 and "valor" not in e.incertos

    def test_nao_sobrescreve_campo_certo(self):
        texto = "Valor: R$ 100,00"
        e = _extracao(texto)
        _mesclar_llm(e, {"valor": 100.0, "banco": "BANCO INVENTADO"}, texto)
        assert e.valor == 100.0 and e.banco is None

    def test_data_so_vale_se_for_uma_das_candidatas_do_texto(self):
        texto = "Data: 28/09/2026"
        e = _extracao(texto)  # sem hora → incerto
        _mesclar_llm(e, {"data_hora": "2026-09-28T22:29:14-03:00"}, texto)
        assert e.data_hora == datetime(2026, 9, 28, tzinfo=BRT)  # a do texto, sem hora inventada
        e2 = _extracao(texto)
        _mesclar_llm(e2, {"data_hora": "2026-01-05T10:00:00-03:00"}, texto)
        assert e2.data_hora == datetime(2026, 9, 28, tzinfo=BRT)

    def test_documento_completo_com_digito_errado_nao_entra(self):
        texto = "Destino\nNome: FULANO DE TAL"
        e = _extracao(texto)
        _mesclar_llm(e, {"favorecido": {"documento": "529.982.247-26"}}, texto)
        assert e.favorecido.documento is None

    def test_lixo_do_llm_nao_quebra(self):
        e = Extracao(favorecido=Favorecido())
        _mesclar_llm(
            e, {"valor": "abc", "data_hora": 5, "pagador": "x", "favorecido": None}, "texto"
        )
        assert e.valor is None and e.data_hora is None
