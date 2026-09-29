import sqlite3
import time
from contextlib import closing
from pathlib import Path

from app.config import Configuracoes
from app.schemas import DadosComprovante

_SEGUNDOS_POR_DIA = 86400


class CacheResultados:
    """Cache SHA-256 → resultado em SQLite. Guarda só o hash e o JSON do resultado (sem
    `texto_bruto`); o arquivo original nunca é gravado."""

    def __init__(self, caminho: str, ttl_dias: int):
        self._caminho = Path(caminho)
        self._ttl = ttl_dias * _SEGUNDOS_POR_DIA
        self._caminho.parent.mkdir(parents=True, exist_ok=True)
        with self._conectar() as con:
            con.execute(
                "CREATE TABLE IF NOT EXISTS cache ("
                "hash TEXT PRIMARY KEY, resultado TEXT NOT NULL, criado_em REAL NOT NULL)"
            )
            con.execute("DELETE FROM cache WHERE criado_em < ?", (time.time() - self._ttl,))

    def _conectar(self) -> sqlite3.Connection:
        con = sqlite3.connect(self._caminho, timeout=10)
        con.execute("PRAGMA journal_mode=WAL")
        return con

    def obter(self, hash_arquivo: str) -> DadosComprovante | None:
        with closing(self._conectar()) as con:
            linha = con.execute(
                "SELECT resultado FROM cache WHERE hash = ? AND criado_em >= ?",
                (hash_arquivo, time.time() - self._ttl),
            ).fetchone()
        return DadosComprovante.model_validate_json(linha[0]) if linha else None

    def salvar(self, dados: DadosComprovante) -> None:
        sem_texto = dados.model_copy(update={"texto_bruto": None})
        with closing(self._conectar()) as con, con:
            con.execute(
                "INSERT OR REPLACE INTO cache (hash, resultado, criado_em) VALUES (?, ?, ?)",
                (dados.hash_arquivo, sem_texto.model_dump_json(), time.time()),
            )


_cache: CacheResultados | None = None


def obter_cache(config: Configuracoes) -> CacheResultados | None:
    global _cache
    if not config.cache_ativo:
        return None
    if _cache is None:
        _cache = CacheResultados(config.cache_db_path, config.cache_ttl_dias)
    return _cache


def definir_cache(cache: CacheResultados | None) -> None:
    global _cache
    _cache = cache
