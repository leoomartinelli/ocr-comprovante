"""Gera prints sintéticos + gabarito.csv para ensaiar o script de avaliação.

Uso: uv run python scripts/gerar_amostras_demo.py [--pasta amostras_demo]
Os dados são fictícios; não substituem os comprovantes reais, mas provam que o fluxo roda.
"""

import argparse
import csv
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PIL import Image

from scripts.sintetico import (
    BRT,
    como_bytes,
    gerar_e2e,
    linhas_estilo_a,
    linhas_estilo_b,
    renderizar,
)

CASOS = [
    # nome, estilo, largura, escuro, formato, opções, valor, favorecido
    ("a_720_claro", "a", 720, False, "PNG", {}, 100.00, "CARLOS MENDES DE ALBUQUERQUE"),
    ("a_1080_claro", "a", 1080, False, "PNG", {}, 250.50, "CARLOS MENDES DE ALBUQUERQUE"),
    ("a_720_escuro", "a", 720, True, "PNG", {}, 1234.56, "ESCOLA EXEMPLO LTDA"),
    ("a_720_escuro_jpeg35", "a", 720, True, "JPEG", {"quality": 35}, 89.90, "ESCOLA EXEMPLO LTDA"),
    (
        "a_540_jpeg50",
        "a",
        540,
        False,
        "JPEG",
        {"quality": 50},
        310.00,
        "CARLOS MENDES DE ALBUQUERQUE",
    ),
    ("a_480_ruim", "a", 480, False, "JPEG", {"quality": 30}, 45.75, "ESCOLA EXEMPLO LTDA"),
    ("b_720_claro", "b", 720, False, "PNG", {}, 780.00, "ESCOLA EXEMPLO LTDA"),
    (
        "b_720_escuro_jpeg40",
        "b",
        720,
        True,
        "JPEG",
        {"quality": 40},
        1500.00,
        "ESCOLA EXEMPLO LTDA",
    ),
    (
        "b_900_claro",
        "b",
        900,
        False,
        "JPEG",
        {"quality": 60},
        99.99,
        "CARLOS MENDES DE ALBUQUERQUE",
    ),
]


def gerar(pasta: Path) -> None:
    pasta.mkdir(parents=True, exist_ok=True)
    with open(pasta / "gabarito.csv", "w", newline="", encoding="utf-8") as f:
        escritor = csv.writer(f)
        escritor.writerow(["arquivo", "valor", "id_transacao", "data", "favorecido"])
        for i, (nome, estilo, largura, escuro, formato, opcoes, valor, favorecido) in enumerate(
            CASOS
        ):
            momento = datetime(2026, 9, 1 + i, 14 + i % 8, 10 + i, 30, tzinfo=BRT)
            e2e = gerar_e2e(momento, semente=i)
            montar = linhas_estilo_a if estilo == "a" else linhas_estilo_b
            linhas = montar(
                valor, momento, e2e, favorecido, "MARIA LUISA DA SILVA", f"COIN{100 + i}"
            )
            base = renderizar(linhas, largura=720, escuro=escuro)
            if largura != 720:  # reduz a imagem inteira, como o WhatsApp faz
                base = base.resize((largura, round(base.height * largura / 720)), Image.LANCZOS)
            extensao = "jpg" if formato == "JPEG" else "png"
            (pasta / f"{nome}.{extensao}").write_bytes(como_bytes(base, formato, **opcoes))
            escritor.writerow(
                [f"{nome}.{extensao}", f"{valor:.2f}", e2e, f"{momento:%d/%m/%Y}", favorecido]
            )
    print(f"{len(CASOS)} amostras sintéticas geradas em {pasta}")


if __name__ == "__main__":
    analisador = argparse.ArgumentParser(description=__doc__)
    analisador.add_argument("--pasta", default="amostras_demo", type=Path)
    gerar(analisador.parse_args().pasta)
