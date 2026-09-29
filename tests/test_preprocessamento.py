import io

import numpy as np
import pytest
from PIL import Image

from app.erros import ErroApi
from app.preprocessamento import preparar
from scripts.sintetico import como_bytes


def _imagem(largura, altura, cor=255, modo="L") -> bytes:
    return como_bytes(Image.new(modo, (largura, altura), cor))


def test_largura_menor_que_800_amplia_2x():
    r = preparar(_imagem(600, 400))
    assert r.escala == 2.0
    assert r.faixas[0].imagem.shape == (800, 1200, 3)


def test_largura_a_partir_de_800_nao_amplia():
    r = preparar(_imagem(800, 400))
    assert r.escala == 1.0 and r.faixas[0].imagem.shape[:2] == (400, 800)


def test_escala_forcada_e_limiar_configuravel():
    assert preparar(_imagem(600, 400), escala=3.0).faixas[0].imagem.shape[1] == 1800
    assert preparar(_imagem(600, 400), largura_minima=500).escala == 1.0


def test_ampliacao_nao_passa_de_2000_px_de_largura():
    r = preparar(_imagem(1500, 400), escala=3.0)
    assert r.faixas[0].imagem.shape[1] == 2000
    assert r.escala == 1.33 and r.escala_pedida == 3.0


def test_largura_enorme_e_reduzida_para_1600():
    assert preparar(_imagem(3000, 500)).faixas[0].imagem.shape[1] == 1600


def test_modo_escuro_e_invertido():
    r = preparar(_imagem(900, 300, cor=20))
    assert r.modo_escuro and r.faixas[0].imagem.mean() > 200


def test_modo_claro_nao_e_invertido():
    r = preparar(_imagem(900, 300, cor=240))
    assert not r.modo_escuro and r.faixas[0].imagem.mean() > 200


def test_print_comprido_vira_faixas_com_sobreposicao():
    r = preparar(_imagem(1000, 5000))
    assert [f.deslocamento_y for f in r.faixas] == [0, 1800, 3600]
    assert [f.imagem.shape[0] for f in r.faixas] == [2000, 2000, 1400]
    assert [f.primeira for f in r.faixas] == [True, False, False]
    assert [f.ultima for f in r.faixas] == [False, False, True]


def test_imagem_curta_e_uma_faixa_so():
    r = preparar(_imagem(1000, 2000))
    assert len(r.faixas) == 1 and r.faixas[0].primeira and r.faixas[0].ultima


def test_exif_gira_a_imagem():
    img = Image.new("L", (900, 300), 255)
    exif = Image.Exif()
    exif[0x0112] = 6  # rotacionar 90° no sentido horário ao exibir
    buffer = io.BytesIO()
    img.save(buffer, "JPEG", exif=exif.tobytes())
    r = preparar(buffer.getvalue(), escala=1.0)
    assert r.faixas[0].imagem.shape[:2] == (900, 300)


def test_png_com_transparencia_vira_fundo_branco():
    r = preparar(como_bytes(Image.new("RGBA", (900, 300), (0, 0, 0, 0))))
    assert not r.modo_escuro and r.faixas[0].imagem.mean() > 250


def test_imagem_corrompida_e_422():
    with pytest.raises(ErroApi) as erro:
        preparar(b"\x89PNG\r\n\x1a\n" + b"lixo")
    assert erro.value.status_code == 422


def test_imagem_com_pixels_demais_e_422():
    gigante = como_bytes(Image.new("1", (20000, 3000), 1))
    with pytest.raises(ErroApi) as erro:
        preparar(gigante)
    assert erro.value.status_code == 422


def test_saida_e_uint8_de_tres_canais():
    imagem = preparar(_imagem(900, 300)).faixas[0].imagem
    assert imagem.dtype == np.uint8 and imagem.ndim == 3 and imagem.shape[2] == 3
