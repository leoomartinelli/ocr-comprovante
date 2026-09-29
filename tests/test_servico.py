import pytest

from app import llm, servico
from app.cache import definir_cache
from app.config import Configuracoes
from app.erros import ErroApi
from app.motor_ocr import definir_motor
from tests.conftest import MotorFalso
from tests.textos import E2E, ESTILO_A_DEGRADADO, ESTILO_A_LIMPO


def _config(tmp_path=None, **campos) -> Configuracoes:
    base = dict(api_key="x", cache_ativo=tmp_path is not None)
    if tmp_path is not None:
        base["cache_db_path"] = str(tmp_path / "cache.db")
    return Configuracoes(**{**base, **campos})


def _largo(imagem) -> bool:
    return imagem.shape[1] > 1500  # segunda tentativa (imagem ampliada)


def test_fluxo_feliz(motor_falso, png):
    r = servico.processar_detalhado(png, _config())
    d = r.dados
    assert (d.metodo, r.tentativas_ocr, r.acionou_llm, r.do_cache) == ("ocr_local", 1, False, False)
    assert d.valor == 100.0 and d.id_transacao == E2E and not d.requer_revisao_humana
    assert d.confianca >= 0.75 and len(d.hash_arquivo) == 64 and d.texto_bruto


def test_cache_devolve_sem_rodar_o_ocr_de_novo(tmp_path, motor_falso, png):
    config = _config(tmp_path)
    primeiro = servico.processar_detalhado(png, config).dados
    chamadas = len(motor_falso.chamadas)
    segundo = servico.processar_detalhado(png, config)
    assert segundo.do_cache and len(motor_falso.chamadas) == chamadas
    assert segundo.dados.metodo == "cache" and segundo.dados.metodo_original == "ocr_local"
    assert segundo.dados.valor == primeiro.valor and segundo.dados.texto_bruto is None


def test_usar_cache_false_ignora_o_cache(tmp_path, motor_falso, png):
    config = _config(tmp_path)
    servico.processar_detalhado(png, config)
    assert not servico.processar_detalhado(png, config, usar_cache=False).do_cache


def test_segunda_leitura_ampliada_quando_a_primeira_duvida(png):
    definir_motor(MotorFalso(lambda img: ESTILO_A_LIMPO if _largo(img) else ESTILO_A_DEGRADADO))
    r = servico.processar_detalhado(png, _config())
    assert r.tentativas_ocr == 2
    assert r.dados.favorecido.nome == "CARLOS MENDES DE ALBUQUERQUE"
    assert not r.dados.requer_revisao_humana


def test_segunda_leitura_pior_nao_substitui_a_primeira(png):
    definir_motor(
        MotorFalso(
            lambda img: "Valor: R$ 100,00 lixo lixo lixo" if _largo(img) else ESTILO_A_DEGRADADO
        )
    )
    r = servico.processar_detalhado(png, _config())
    assert r.tentativas_ocr >= 2 and r.dados.id_transacao == E2E


def test_nao_relê_quando_a_primeira_leitura_e_confiavel(motor_falso, png):
    servico.processar_detalhado(png, _config())
    assert len(motor_falso.chamadas) == 1


def test_imagem_sem_texto_legivel_e_422(png):
    definir_motor(MotorFalso(""))
    with pytest.raises(ErroApi) as erro:
        servico.processar_detalhado(png, _config())
    assert erro.value.status_code == 422


def test_erro_do_ocr_nao_e_guardado_no_cache(tmp_path, png):
    definir_motor(MotorFalso(""))
    with pytest.raises(ErroApi):
        servico.processar_detalhado(png, _config(tmp_path))
    definir_motor(MotorFalso(ESTILO_A_LIMPO))
    assert not servico.processar_detalhado(png, _config(tmp_path)).do_cache


class TestFallbackLLM:
    PARTIDO = f"Valor R$ 10,00\nControle {E2E[:21]} AbcdEfGhIjK"  # E2E partido, sem dígito no fim
    REAL = E2E[:21] + "AbcdEfGhIjK"

    def _preparar(self, monkeypatch, sugestao=None, texto=None):
        definir_motor(MotorFalso(texto or self.PARTIDO))
        chamadas = []

        def falso(texto_ocr, config):
            chamadas.append(texto_ocr)
            return llm.RespostaLLM(sugestao or {"id_transacao": self.REAL}, 300, 80)

        monkeypatch.setattr(llm, "consultar", falso)
        return chamadas

    def test_desligado_por_padrao(self, monkeypatch, png):
        chamadas = self._preparar(monkeypatch)
        r = servico.processar_detalhado(png, _config())
        assert chamadas == [] and r.dados.metodo == "ocr_local" and r.dados.id_transacao is None

    def test_ligado_e_confianca_baixa_aciona_e_mescla(self, monkeypatch, png):
        chamadas = self._preparar(monkeypatch)
        r = servico.processar_detalhado(png, _config(llm_fallback_ativo=True))
        assert len(chamadas) == 1 and r.acionou_llm
        assert r.dados.metodo == "ocr_local+llm" and r.dados.id_transacao == self.REAL
        assert (r.llm_tokens_entrada, r.llm_tokens_saida) == (300, 80)

    def test_nao_aciona_com_confianca_alta(self, monkeypatch, png):
        chamadas = self._preparar(monkeypatch, texto=ESTILO_A_LIMPO)
        r = servico.processar_detalhado(png, _config(llm_fallback_ativo=True))
        assert chamadas == [] and r.dados.metodo == "ocr_local"

    def test_nao_aciona_quando_o_problema_e_so_nome_colado(self, monkeypatch, png):
        # confiança fica acima do limiar: vai para revisão humana, sem gastar LLM
        chamadas = self._preparar(monkeypatch, texto=ESTILO_A_DEGRADADO)
        r = servico.processar_detalhado(png, _config(llm_fallback_ativo=True))
        assert chamadas == [] and r.dados.requer_revisao_humana

    def test_parametro_usar_llm_sobrepoe_a_configuracao(self, monkeypatch, png):
        chamadas = self._preparar(monkeypatch)
        servico.processar_detalhado(png, _config(llm_fallback_ativo=True), usar_llm=False)
        assert chamadas == []
        servico.processar_detalhado(png, _config(), usar_llm=True)
        assert len(chamadas) == 1

    def test_falha_do_llm_mantem_o_resultado_local(self, monkeypatch, png):
        definir_motor(MotorFalso(self.PARTIDO))
        monkeypatch.setattr(llm, "consultar", lambda texto, config: None)
        r = servico.processar_detalhado(png, _config(llm_fallback_ativo=True))
        assert not r.acionou_llm and r.dados.metodo == "ocr_local" and r.dados.requer_revisao_humana

    def test_llm_nao_inventa_o_que_nao_esta_no_texto(self, monkeypatch, png):
        self._preparar(monkeypatch, sugestao={"id_transacao": E2E, "valor": 999.0})
        r = servico.processar_detalhado(png, _config(llm_fallback_ativo=True))
        assert r.dados.id_transacao is None and r.dados.valor == 10.0

    def test_resultado_com_llm_vai_para_o_cache_com_a_camada_original(
        self, tmp_path, monkeypatch, png
    ):
        self._preparar(monkeypatch)
        config = _config(tmp_path, llm_fallback_ativo=True)
        servico.processar_detalhado(png, config)
        definir_cache(None)
        assert servico.processar_detalhado(png, config).dados.metodo_original == "ocr_local+llm"
