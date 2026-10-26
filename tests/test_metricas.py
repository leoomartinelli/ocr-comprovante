from collections.abc import Iterator
from datetime import date

import pytest
from fastapi.testclient import TestClient

from app.config import obter_configuracoes
from app.main import app
from app.metricas import Evento, Metricas, definir_metricas, resumir
from tests.conftest import CHAVE

# 2026-10-10 12:00 em Brasília (15:00 UTC)
AGORA = 1791644400.0


@pytest.fixture
def metricas(tmp_path, monkeypatch) -> Iterator[Metricas]:
    monkeypatch.setenv("METRICAS_ATIVO", "true")
    obter_configuracoes.cache_clear()
    m = Metricas(str(tmp_path / "metricas.db"), retencao_dias=365)
    definir_metricas(m)
    yield m
    obter_configuracoes.cache_clear()


def _enviar(client, png, headers=CHAVE):
    return client.post(
        "/ocr/comprovante", files={"arquivo": ("p.png", png, "image/png")}, headers=headers
    )


def test_sucesso_e_registrado_sem_dados_pessoais(metricas, client, png, motor_falso):
    assert _enviar(client, png).status_code == 200
    (evento,) = metricas.eventos(0)
    assert evento.status == 200 and evento.metodo == "ocr_local"
    assert evento.revisao is False and evento.valor == 100.0
    assert "identificador" in evento.encontrados and "favorecido" in evento.encontrados
    assert evento.banco == "BCO EXEMPLO S.A." and evento.tentativas == 1
    salvo = metricas._conectar().execute("SELECT dados FROM eventos").fetchone()[0]
    assert "CARLOS" not in salvo and "MARIA" not in salvo and "123.456" not in salvo


def test_erros_sao_registrados_com_o_motivo(metricas, client, png):
    _enviar(client, png, headers={})
    _enviar(client, b"%PDF-1.4")
    eventos = metricas.eventos(0)
    assert [(e.status, e.motivo) for e in eventos] == [
        (401, "API key ausente ou inválida."),
        (400, "PDF não é aceito neste endpoint. Envie um print (JPG, PNG ou WEBP)."),
    ]


def test_erro_inesperado_e_registrado_como_500(metricas, png, monkeypatch):
    from app import main

    def quebra(*args, **kwargs):
        raise RuntimeError("x")

    monkeypatch.setattr(main, "processar_detalhado", quebra)
    _enviar(TestClient(app, raise_server_exceptions=False), png)
    (evento,) = metricas.eventos(0)
    assert evento.status == 500 and evento.motivo == "Erro interno ao processar o comprovante."


def test_falha_na_metrica_nao_derruba_a_resposta(metricas, client, png, motor_falso, monkeypatch):
    def quebra(_):
        raise OSError("disco cheio")

    monkeypatch.setattr(metricas, "registrar", quebra)
    assert _enviar(client, png).status_code == 200


def test_outras_rotas_nao_sao_registradas(metricas, client):
    client.get("/saude")
    assert metricas.eventos(0) == []


def test_resumo_conta_desfechos_campos_e_dias():
    eventos = [
        Evento(422, 900, motivo="Não foi possível ler texto na imagem.", criado_em=AGORA - 86400),
        Evento(200, 3000, metodo="ocr_local", confianca=0.9, revisao=False, valor=100.0,
               banco="NU", encontrados=["valor", "identificador"], criado_em=AGORA),
        Evento(200, 5000, metodo="ocr_local", confianca=0.5, revisao=True, valor=50.0,
               encontrados=["valor"], campos_incertos=["id_transacao"], criado_em=AGORA),
        Evento(200, 200, metodo="cache", confianca=0.9, revisao=False, valor=100.0,
               banco="NU", encontrados=["valor", "identificador"], criado_em=AGORA),
    ]  # fmt: skip
    r = resumir(eventos, date(2026, 10, 9), 2)
    t = r["totais"]
    assert (t["recebidos"], t["sucesso"], t["erro"]) == (4, 3, 1)
    assert (t["automatico"], t["revisao"], t["cache"], t["identificadores"]) == (2, 1, 1, 2)
    assert t["valor_total"] == 150.0  # o repetido (cache) não soma de novo
    assert t["duracao_mediana_ms"] == 3000  # cache fica fora do tempo de leitura
    assert r["por_dia"] == [
        {"dia": "2026-10-09", "automatico": 0, "revisao": 0, "erro": 1},
        {"dia": "2026-10-10", "automatico": 2, "revisao": 1, "erro": 0},
    ]
    assert {c["campo"]: c["encontrados"] for c in r["campos"]}["identificador"] == 2
    assert r["incertos"] == [{"campo": "id_transacao", "quantidade": 1}]
    assert r["erros"] == [
        {"status": 422, "motivo": "Não foi possível ler texto na imagem.", "quantidade": 1}
    ]
    assert r["bancos"] == [
        {"banco": "NU", "quantidade": 2},
        {"banco": "Não identificado", "quantidade": 1},
    ]
    assert [f["quantidade"] for f in r["confianca"]][5] == 1
    assert r["ultimos"][0]["quando"].startswith("2026-10-10T12:00:00")


def test_resumo_respeita_o_periodo(metricas):
    metricas.registrar(Evento(200, 1, revisao=False, criado_em=AGORA))
    metricas.registrar(Evento(200, 1, revisao=False, criado_em=AGORA - 10 * 86400))
    assert metricas.resumo(7, agora=AGORA)["totais"]["recebidos"] == 1
    assert metricas.resumo(30, agora=AGORA)["totais"]["recebidos"] == 2


def test_painel_dados_exige_api_key(metricas, client):
    assert client.get("/painel/dados").status_code == 401
    r = client.get("/painel/dados?dias=7", headers=CHAVE)
    assert r.status_code == 200
    assert r.json()["periodo_dias"] == 7 and r.json()["limiar"] == 0.75


def test_painel_dados_com_metricas_desligadas(client):
    assert client.get("/painel/dados", headers=CHAVE).status_code == 404


def test_pagina_do_painel_abre_sem_chave_e_sem_dados(client):
    r = client.get("/painel")
    assert r.status_code == 200 and "Painel do OCR" in r.text
