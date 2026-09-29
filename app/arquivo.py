import hashlib
from enum import StrEnum


class TipoArquivo(StrEnum):
    JPEG = "jpeg"
    PNG = "png"
    WEBP = "webp"


def eh_pdf(conteudo: bytes) -> bool:
    return b"%PDF-" in conteudo[:1024]


def detectar_tipo(conteudo: bytes) -> TipoArquivo | None:
    """Identifica o tipo pelos magic bytes, ignorando extensão e Content-Type."""
    if conteudo.startswith(b"\xff\xd8\xff"):
        return TipoArquivo.JPEG
    if conteudo.startswith(b"\x89PNG\r\n\x1a\n"):
        return TipoArquivo.PNG
    if conteudo[:4] == b"RIFF" and conteudo[8:12] == b"WEBP":
        return TipoArquivo.WEBP
    return None


def calcular_hash(conteudo: bytes) -> str:
    return hashlib.sha256(conteudo).hexdigest()
