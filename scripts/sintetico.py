"""Gera prints sintéticos de comprovante Pix (dados fictícios) para testes e demonstração."""

import io
import random
import string
from datetime import UTC, datetime, timedelta, timezone

from PIL import Image, ImageDraw, ImageFont

FONTES = [
    "C:/Windows/Fonts/arial.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans.ttf",
    "/System/Library/Fonts/Supplemental/Arial.ttf",
]
BRT = timezone(timedelta(hours=-3))

Linha = tuple[str, int]  # (texto, tamanho da fonte)


def caminho_fonte() -> str | None:
    from pathlib import Path

    return next((f for f in FONTES if Path(f).exists()), None)


def gerar_e2e(momento: datetime, semente: int) -> str:
    """E2E de formato válido (E + ISPB + aaaammddhhmm em UTC + 11 alfanuméricos)."""
    aleatorio = random.Random(semente)
    cauda = "".join(aleatorio.choices(string.ascii_letters + string.digits, k=11))
    return f"E12345678{momento.astimezone(UTC):%Y%m%d%H%M}{cauda}"


def _formatar_valor(valor: float) -> str:
    return f"R$ {valor:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def linhas_estilo_a(valor, momento, e2e, favorecido, pagador, identificador) -> list[Linha]:
    """Layout com "Rótulo: valor" em uma linha (estilo app de banco tradicional)."""
    return [
        ("Comprovante de pagamento Pix", 34),
        (f"Valor: {_formatar_valor(valor)}", 40),
        (f"{momento:%d/%m/%Y - %H:%M:%S}", 28),
        ("Dados de quem recebeu", 30),
        (f"Nome: {favorecido}", 26),
        ("CPF/CNPJ: ***.123.456-**", 26),
        ("Instituição: BCO EXEMPLO S.A.", 26),
        ("Dados do pagamento", 30),
        ("Tipo: Pix", 26),
        (f"Valor: {_formatar_valor(valor)}", 26),
        (f"Identificador: {identificador}", 26),
        (f"Data do débito: {momento:%d/%m/%Y - %H:%M:%S}", 26),
        (f"Número de controle: {e2e}", 22),
        ("Dados de quem pagou", 30),
        (f"Nome: {pagador}", 26),
        ("CPF: ***.654.321-**", 26),
    ]


def linhas_estilo_b(valor, momento, e2e, favorecido, pagador, identificador) -> list[Linha]:
    """Layout com rótulo numa linha e valor na seguinte (estilo app de banco digital)."""
    return [
        ("Transferência enviada", 34),
        (_formatar_valor(valor), 44),
        (f"{momento:%d/%m/%Y} às {momento:%H:%M:%S}", 26),
        ("Destino", 30),
        ("Nome", 22),
        (favorecido, 26),
        ("CPF", 22),
        ("***.123.456-**", 26),
        ("Instituição", 22),
        ("PAGAMENTOS EXEMPLO S.A.", 26),
        ("Origem", 30),
        ("Nome", 22),
        (pagador, 26),
        ("ID da transação", 22),
        (e2e, 22),
    ]


def renderizar(
    linhas: list[Linha], largura=720, escuro=False, espaco=22, altura_minima=900
) -> Image.Image:
    fonte = caminho_fonte()
    if fonte is None:
        raise RuntimeError("nenhuma fonte TrueType encontrada para gerar amostras")
    altura = max(altura_minima, 60 + sum(t + espaco for _, t in linhas))
    fundo, texto = ((18, 18, 18), (235, 235, 235)) if escuro else ((255, 255, 255), (0, 0, 0))
    img = Image.new("RGB", (largura, altura), fundo)
    desenho = ImageDraw.Draw(img)
    y = 30
    for conteudo, tamanho in linhas:
        desenho.text((30, y), conteudo, fill=texto, font=ImageFont.truetype(fonte, tamanho))
        y += tamanho + espaco
    return img


def como_bytes(img: Image.Image, formato="PNG", **opcoes) -> bytes:
    buffer = io.BytesIO()
    img.save(buffer, formato, **opcoes)
    return buffer.getvalue()
