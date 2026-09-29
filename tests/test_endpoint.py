from PIL import Image

from scripts.sintetico import como_bytes
from tests.conftest import CHAVE
from tests.textos import E2E


def _enviar(client, conteudo, nome="print.png", tipo="image/png", headers=CHAVE):
    return client.post(
        "/ocr/comprovante", files={"arquivo": (nome, conteudo, tipo)}, headers=headers
    )


def test_sem_api_key_retorna_401(client, png):
    r = _enviar(client, png, headers={})
    assert r.status_code == 401
    assert r.json() == {"success": False, "message": "API key ausente ou inválida."}


def test_api_key_errada_retorna_401(client, png):
    assert _enviar(client, png, headers={"X-API-Key": "errada"}).status_code == 401


def test_api_key_com_caractere_nao_ascii_nao_quebra(client, png):
    r = _enviar(client, png, headers={"X-API-Key": "chavé".encode("latin-1")})
    assert r.status_code == 401


def test_401_vem_antes_de_qualquer_validacao_do_arquivo(client):
    assert _enviar(client, b"", headers={}).status_code == 401


def test_print_segue_o_contrato_fechado(client, png, motor_falso):
    r = _enviar(client, png)
    assert r.status_code == 200
    corpo = r.json()
    assert corpo["success"] is True
    d = corpo["data"]
    assert set(d) == {
        "valor", "data_hora", "id_transacao", "pagador", "favorecido", "banco", "identificador",
        "confianca", "campos_incertos", "requer_revisao_humana", "metodo", "metodo_original",
        "hash_arquivo", "texto_bruto",
    }  # fmt: skip
    assert d["valor"] == 100.0 and d["id_transacao"] == E2E
    assert d["data_hora"] == "2026-09-28T22:29:14-03:00"
    assert d["pagador"] == {"nome": "MARIA LUISA DA SILVA", "documento": "***.654.321-**"}
    assert d["favorecido"] == {
        "nome": "CARLOS MENDES DE ALBUQUERQUE",
        "documento": "***.123.456-**",
        "chave_pix": None,
    }
    assert d["identificador"] == "COIN999" and d["banco"] == "BCO EXEMPLO S.A."
    assert d["metodo"] == "ocr_local" and d["metodo_original"] is None
    assert d["requer_revisao_humana"] is False and d["campos_incertos"] == []
    assert len(d["hash_arquivo"]) == 64


def test_pdf_e_recusado_com_mensagem_clara(client):
    r = _enviar(client, b"%PDF-1.4 conteudo", "c.pdf", "application/pdf")
    assert r.status_code == 400 and "PDF" in r.json()["message"]


def test_extensao_mentirosa_e_rejeitada_pelos_magic_bytes(client):
    r = _enviar(client, b"MZ\x90\x00 executavel", "print.png", "image/png")
    assert r.status_code == 400 and r.json()["success"] is False


def test_tipo_real_vale_mais_que_a_extensao(client, motor_falso):
    jpeg = como_bytes(Image.new("RGB", (900, 600), "white"), "JPEG")
    assert _enviar(client, jpeg, "print.pdf", "application/pdf").status_code == 200


def test_webp_e_aceito(client, motor_falso):
    webp = como_bytes(Image.new("RGB", (900, 600), "white"), "WEBP")
    assert _enviar(client, webp, "print.webp", "image/webp").status_code == 200


def test_arquivo_vazio_retorna_400(client):
    assert _enviar(client, b"").status_code == 400


def test_sem_campo_arquivo_retorna_400(client):
    r = client.post("/ocr/comprovante", headers=CHAVE)
    assert r.status_code == 400 and r.json()["success"] is False


def test_arquivo_acima_do_limite_retorna_413(client, png, monkeypatch):
    from app.config import obter_configuracoes

    monkeypatch.setattr(obter_configuracoes(), "tamanho_maximo_mb", 0)
    assert _enviar(client, png).status_code == 413


def test_imagem_corrompida_retorna_422(client):
    assert _enviar(client, b"\x89PNG\r\n\x1a\n" + b"lixo").status_code == 422


def test_print_sem_texto_legivel_retorna_422(client, png):
    from app.motor_ocr import definir_motor
    from tests.conftest import MotorFalso

    definir_motor(MotorFalso(""))
    r = _enviar(client, png)
    assert r.status_code == 422 and r.json()["success"] is False


def test_rota_inexistente_tambem_usa_o_formato_de_erro(client):
    r = client.get("/nao-existe")
    assert r.status_code == 404 and r.json()["success"] is False


def test_erro_inesperado_nao_vaza_detalhes(png, monkeypatch):
    from fastapi.testclient import TestClient

    from app import main

    def quebra(*args, **kwargs):
        raise RuntimeError("segredo interno")

    monkeypatch.setattr(main, "processar", quebra)
    resposta = _enviar(TestClient(main.app, raise_server_exceptions=False), png)
    assert resposta.status_code == 500 and "segredo" not in resposta.text
    assert resposta.json() == {
        "success": False,
        "message": "Erro interno ao processar o comprovante.",
    }


def test_saude_nao_exige_chave(client):
    assert client.get("/saude").json() == {"status": "ok"}
