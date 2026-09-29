import sqlite3

from app.cache import CacheResultados
from app.schemas import DadosComprovante, Favorecido


def _dados(hash_arquivo="a" * 64, **campos) -> DadosComprovante:
    base = dict(
        valor=100.0,
        favorecido=Favorecido(nome="ESCOLA EXEMPLO", documento="***.123.456-**"),
        metodo="ocr_local",
        hash_arquivo=hash_arquivo,
        texto_bruto="Nome: ESCOLA EXEMPLO\nCPF: ***.123.456-**",
    )
    return DadosComprovante(**{**base, **campos})


def test_guarda_e_devolve_o_resultado(tmp_path):
    cache = CacheResultados(str(tmp_path / "c.db"), ttl_dias=30)
    cache.salvar(_dados())
    lido = cache.obter("a" * 64)
    assert lido.valor == 100.0 and lido.favorecido.nome == "ESCOLA EXEMPLO"
    assert lido.metodo == "ocr_local"


def test_nao_grava_o_texto_bruto_nem_o_arquivo(tmp_path):
    caminho = tmp_path / "c.db"
    cache = CacheResultados(str(caminho), ttl_dias=30)
    cache.salvar(_dados())
    assert cache.obter("a" * 64).texto_bruto is None
    bruto = sqlite3.connect(caminho).execute("SELECT resultado FROM cache").fetchone()[0]
    assert "Nome: ESCOLA EXEMPLO" not in bruto and 'texto_bruto":null' in bruto.replace(" ", "")


def test_hash_desconhecido_devolve_none(tmp_path):
    assert CacheResultados(str(tmp_path / "c.db"), 30).obter("b" * 64) is None


def test_expira_pelo_ttl(tmp_path):
    cache = CacheResultados(str(tmp_path / "c.db"), ttl_dias=0)
    cache.salvar(_dados())
    assert cache.obter("a" * 64) is None


def test_expirados_sao_apagados_ao_abrir(tmp_path):
    caminho = str(tmp_path / "c.db")
    CacheResultados(caminho, ttl_dias=30).salvar(_dados())
    CacheResultados(caminho, ttl_dias=0)
    assert sqlite3.connect(caminho).execute("SELECT COUNT(*) FROM cache").fetchone()[0] == 0


def test_cria_a_pasta_que_nao_existe(tmp_path):
    cache = CacheResultados(str(tmp_path / "dados" / "fundo" / "c.db"), ttl_dias=1)
    cache.salvar(_dados())
    assert cache.obter("a" * 64) is not None


def test_salvar_o_mesmo_hash_substitui(tmp_path):
    cache = CacheResultados(str(tmp_path / "c.db"), 30)
    cache.salvar(_dados(valor=1.0))
    cache.salvar(_dados(valor=2.0))
    assert cache.obter("a" * 64).valor == 2.0
