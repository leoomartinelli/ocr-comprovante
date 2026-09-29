import io
from dataclasses import dataclass

import numpy as np
from PIL import Image, ImageOps, UnidentifiedImageError

from app.erros import ErroApi

MAX_PIXELS = 40_000_000
LARGURA_MAXIMA = 1600
LARGURA_LIMITE_OCR = 2000  # acima disso o RapidOCR reduz a imagem, anulando a ampliação
ALTURA_FAIXA = 2000
SOBREPOSICAO = 200
BRILHO_MODO_ESCURO = 110


@dataclass(frozen=True)
class Faixa:
    imagem: np.ndarray
    deslocamento_y: int
    primeira: bool
    ultima: bool


@dataclass(frozen=True)
class ImagemPreparada:
    faixas: list[Faixa]
    escala: float  # ampliação efetivamente aplicada
    escala_pedida: float
    modo_escuro: bool


def _abrir(conteudo: bytes) -> Image.Image:
    try:
        img = Image.open(io.BytesIO(conteudo))
        if img.width * img.height > MAX_PIXELS:
            raise ErroApi(422, "Imagem grande demais para processar.")
        img.load()
    except ErroApi:
        raise
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError) as exc:
        raise ErroApi(422, "Não foi possível abrir a imagem.") from exc
    return img


def _para_cinza(img: Image.Image) -> Image.Image:
    img = ImageOps.exif_transpose(img)
    if img.mode in ("RGBA", "LA", "PA") or (img.mode == "P" and "transparency" in img.info):
        rgba = img.convert("RGBA")
        fundo = Image.new("RGBA", rgba.size, "white")
        fundo.alpha_composite(rgba)
        img = fundo
    return img.convert("L")


def _dividir(cinza: Image.Image) -> list[Faixa]:
    largura, altura = cinza.size
    faixas: list[Faixa] = []
    inicio = 0
    while True:
        fim = min(inicio + ALTURA_FAIXA, altura)
        recorte = np.asarray(cinza.crop((0, inicio, largura, fim)))
        faixas.append(
            Faixa(
                imagem=np.stack([recorte] * 3, axis=-1),
                deslocamento_y=inicio,
                primeira=inicio == 0,
                ultima=fim == altura,
            )
        )
        if fim == altura:
            return faixas
        inicio = fim - SOBREPOSICAO


def preparar(
    conteudo: bytes, *, largura_minima: int = 800, escala: float | None = None
) -> ImagemPreparada:
    """EXIF → cinza → inverte modo escuro → amplia se estreita → divide se comprida.

    `escala` força o fator de ampliação (usado na segunda tentativa do pipeline)."""
    cinza = _para_cinza(_abrir(conteudo))

    if cinza.width > LARGURA_MAXIMA:
        proporcao = LARGURA_MAXIMA / cinza.width
        cinza = cinza.resize((LARGURA_MAXIMA, round(cinza.height * proporcao)), Image.LANCZOS)

    modo_escuro = float(np.asarray(cinza).mean()) < BRILHO_MODO_ESCURO
    if modo_escuro:
        cinza = ImageOps.invert(cinza)

    pedida = escala if escala is not None else (2.0 if cinza.width < largura_minima else 1.0)
    efetiva = max(1.0, min(pedida, LARGURA_LIMITE_OCR / cinza.width))
    if efetiva != 1.0:
        cinza = cinza.resize(
            (round(cinza.width * efetiva), round(cinza.height * efetiva)), Image.LANCZOS
        )

    return ImagemPreparada(
        faixas=_dividir(cinza),
        escala=round(efetiva, 2),
        escala_pedida=pedida,
        modo_escuro=modo_escuro,
    )
