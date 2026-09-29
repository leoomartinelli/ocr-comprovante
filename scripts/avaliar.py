"""Avalia o OCR numa pasta de amostras com um gabarito.csv.

Uso: uv run python scripts/avaliar.py [--pasta amostras] [--com-llm] [--textos]

gabarito.csv (vírgula ou ponto e vírgula) com as colunas:
    arquivo, valor, id_transacao, data, favorecido
- valor: 1234.56 ou 1.234,56 · data: dd/mm/aaaa ou aaaa-mm-dd
- deixe a célula vazia para não avaliar aquele campo naquele arquivo.

Sem --com-llm o script NÃO chama o LLM: ele mede quantos arquivos iriam para o fallback.
Com --com-llm (exige ANTHROPIC_API_KEY, gasta dinheiro) roda o fallback só nesses arquivos.
"""

import argparse
import csv
import os
import sys
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime
from difflib import SequenceMatcher
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("API_KEY", "avaliacao")

from app.config import Configuracoes
from app.erros import ErroApi
from app.servico import Resultado, processar_detalhado
from app.validadores import normalizar_nome

EXTENSOES = {".jpg", ".jpeg", ".png", ".webp"}
CAMPOS = ("valor", "id_transacao", "data", "favorecido")
USD_ENTRADA_POR_MILHAO, USD_SAIDA_POR_MILHAO = 1.00, 5.00  # Claude Haiku 4.5

ERRO = "erro (422/ilegível)"
ABAIXO = "abaixo do limiar (iria ao LLM)"
DUVIDA = "confiança ok, campo crítico duvidoso (revisão humana, sem LLM)"
COM_LLM = "ocr_local + LLM"


@dataclass
class Esperado:
    valor: float | None = None
    id_transacao: str | None = None
    data: date | None = None
    favorecido: str | None = None


@dataclass
class Linha:
    arquivo: str
    esperado: Esperado
    resultado: Resultado | None = None
    erro: str | None = None
    segundos: float = 0.0
    acertos: dict[str, bool | None] = field(default_factory=dict)
    favorecido_aproximado: bool | None = None


def _parse_valor(texto: str) -> float:
    texto = texto.strip().replace("R$", "").strip()
    if "," in texto:
        texto = texto.replace(".", "").replace(",", ".")
    return float(texto)


def _parse_data(texto: str) -> date:
    texto = texto.strip()
    formato = "%d/%m/%Y" if "/" in texto else "%Y-%m-%d"
    return datetime.strptime(texto, formato).date()


def ler_gabarito(caminho: Path) -> dict[str, Esperado]:
    conteudo = caminho.read_text(encoding="utf-8-sig")
    delimitador = (
        ";"
        if conteudo.split("\n", 1)[0].count(";") > conteudo.split("\n", 1)[0].count(",")
        else ","
    )
    linhas = csv.DictReader(conteudo.splitlines(), delimiter=delimitador)
    gabarito = {}
    for r in linhas:
        arquivo = (r.get("arquivo") or "").strip()
        if not arquivo:
            continue
        gabarito[arquivo] = Esperado(
            valor=_parse_valor(r["valor"]) if (r.get("valor") or "").strip() else None,
            id_transacao=(r.get("id_transacao") or "").strip() or None,
            data=_parse_data(r["data"]) if (r.get("data") or "").strip() else None,
            favorecido=(r.get("favorecido") or "").strip() or None,
        )
    return gabarito


def _nome_parecido(obtido: str | None, esperado: str) -> bool:
    if not obtido:
        return False
    a, b = normalizar_nome(obtido), normalizar_nome(esperado)
    return a == b or a in b or b in a or SequenceMatcher(None, a, b).ratio() >= 0.85


def comparar(linha: Linha) -> None:
    esperado, dados = linha.esperado, linha.resultado.dados if linha.resultado else None
    obtido_data = dados.data_hora.date() if dados and dados.data_hora else None
    if esperado.valor is not None:
        ok = (
            dados is not None
            and dados.valor is not None
            and abs(dados.valor - esperado.valor) < 0.005
        )
        linha.acertos["valor"] = ok
    if esperado.id_transacao is not None:
        linha.acertos["id_transacao"] = bool(dados and dados.id_transacao == esperado.id_transacao)
    if esperado.data is not None:
        linha.acertos["data"] = obtido_data == esperado.data
    if esperado.favorecido is not None:
        nome = dados.favorecido.nome if dados else None
        linha.acertos["favorecido"] = bool(
            nome and normalizar_nome(nome) == normalizar_nome(esperado.favorecido)
        )
        linha.favorecido_aproximado = _nome_parecido(nome, esperado.favorecido)


def _categoria(linha: Linha, limiar: float) -> str:
    if linha.resultado is None:
        return ERRO
    dados = linha.resultado.dados
    if dados.metodo == "ocr_local+llm":
        return COM_LLM
    if dados.confianca < limiar:
        return ABAIXO
    if dados.requer_revisao_humana:
        return DUVIDA
    if linha.resultado.tentativas_ocr == 1:
        return "ocr_local, 1ª leitura"
    return f"ocr_local, releitura ampliada ({linha.resultado.tentativas_ocr} leituras)"


def _pct(parte: int, total: int) -> str:
    return f"{100 * parte / total:5.1f}% ({parte}/{total})" if total else "    —"


def _acuracia(linhas: list[Linha]) -> str:
    partes = []
    for campo in CAMPOS:
        avaliados = [ln.acertos[campo] for ln in linhas if ln.acertos.get(campo) is not None]
        partes.append(f"{campo} {_pct(sum(avaliados), len(avaliados))}")
    return " | ".join(partes)


def _confiante_e_errada(linha: Linha) -> list[str]:
    """Campos críticos errados num resultado que NÃO pediu revisão humana."""
    if linha.resultado is None or linha.resultado.dados.requer_revisao_humana:
        return []
    return [c for c in ("valor", "id_transacao", "data") if linha.acertos.get(c) is False]


def avaliar(pasta: Path, com_llm: bool, salvar_textos: bool) -> int:
    config = Configuracoes()
    gabarito = ler_gabarito(pasta / "gabarito.csv")
    arquivos = sorted(p for p in pasta.iterdir() if p.suffix.lower() in EXTENSOES)
    if not arquivos:
        print(f"Nenhuma imagem encontrada em {pasta}")
        return 1
    sem_gabarito = [p.name for p in arquivos if p.name not in gabarito]
    if sem_gabarito:
        print(
            f"Aviso: {len(sem_gabarito)} imagem(ns) sem linha no gabarito serão ignoradas: {sem_gabarito}"
        )
    linhas: list[Linha] = []
    pasta_textos = pasta / "textos"
    if salvar_textos:
        pasta_textos.mkdir(exist_ok=True)

    for caminho in (p for p in arquivos if p.name in gabarito):
        linha = Linha(caminho.name, gabarito[caminho.name])
        inicio = time.monotonic()
        try:
            linha.resultado = processar_detalhado(
                caminho.read_bytes(), config, usar_cache=False, usar_llm=False
            )
        except ErroApi as exc:
            linha.erro = exc.message
        linha.segundos = time.monotonic() - inicio
        if salvar_textos and linha.resultado:
            (pasta_textos / f"{caminho.stem}.txt").write_text(
                linha.resultado.dados.texto_bruto or "", encoding="utf-8"
            )
        comparar(linha)
        linhas.append(linha)
        print(
            f"  {caminho.name:32s} {linha.segundos:5.1f}s {_categoria(linha, config.confianca_minima)}"
        )

    total = len(linhas)
    print(f"\n=== {total} arquivos em {pasta} ===")
    print(f"Tempo médio por arquivo: {sum(ln.segundos for ln in linhas) / total:.1f}s")
    print("\nAcurácia por campo (OCR local, sem LLM):")
    print(" ", _acuracia(linhas))
    aprox = [ln.favorecido_aproximado for ln in linhas if ln.favorecido_aproximado is not None]
    print(
        f"  favorecido aproximado (≥85% parecido, sem espaços/acentos): {_pct(sum(aprox), len(aprox))}"
    )

    print("\nPor camada:")
    por_categoria: dict[str, list[Linha]] = defaultdict(list)
    for ln in linhas:
        por_categoria[_categoria(ln, config.confianca_minima)].append(ln)
    for categoria, grupo in sorted(por_categoria.items()):
        print(f"  {categoria:36s} {_pct(len(grupo), total)}")
        print(f"      {_acuracia(grupo)}")

    abaixo = por_categoria.get(ABAIXO, []) + por_categoria.get(ERRO, [])
    print(
        f"\nFallback LLM necessário: {_pct(len(abaixo), total)}  (limiar de confiança {config.confianca_minima})"
    )

    perigosas = [(ln.arquivo, _confiante_e_errada(ln)) for ln in linhas if _confiante_e_errada(ln)]
    print(f"Confiantes e ERRADAS (sem pedir revisão): {len(perigosas)}  ← o número mais importante")
    for arquivo, campos in perigosas:
        print(f"    {arquivo}: {', '.join(campos)}")

    if com_llm:
        _avaliar_llm(config, pasta, linhas, abaixo, total)

    _salvar_detalhes(pasta / "resultado.csv", linhas, config.confianca_minima)
    print(f"\nDetalhe por arquivo em {pasta / 'resultado.csv'}")
    return 0


def _avaliar_llm(config, pasta: Path, linhas: list[Linha], abaixo: list[Linha], total: int) -> None:
    if not config.anthropic_api_key:
        print("\n--com-llm: defina ANTHROPIC_API_KEY (no .env ou no ambiente).")
        return
    print(f"\nRodando o fallback LLM em {len(abaixo)} arquivo(s)...")
    tokens_entrada = tokens_saida = acionados = 0
    for ln in abaixo:
        if ln.resultado is None:
            continue
        novo = processar_detalhado(
            (pasta / ln.arquivo).read_bytes(), config, usar_cache=False, usar_llm=True
        )
        acionados += novo.acionou_llm
        tokens_entrada += novo.llm_tokens_entrada
        tokens_saida += novo.llm_tokens_saida
        ln.resultado = novo
        ln.acertos.clear()
        comparar(ln)
    custo = (
        tokens_entrada / 1e6 * USD_ENTRADA_POR_MILHAO + tokens_saida / 1e6 * USD_SAIDA_POR_MILHAO
    )
    print(f"Taxa de acionamento do LLM: {_pct(acionados, total)}")
    print(
        f"Tokens: {tokens_entrada} entrada / {tokens_saida} saída · custo ≈ US$ {custo:.4f} no lote"
    )
    if acionados:
        print(f"  ≈ US$ {custo / acionados:.5f} por arquivo que usou o LLM")
    print("Acurácia por campo após o LLM (todos os arquivos):")
    print(" ", _acuracia(linhas))


def _salvar_detalhes(caminho: Path, linhas: list[Linha], limiar: float) -> None:
    with open(caminho, "w", newline="", encoding="utf-8") as f:
        escritor = csv.writer(f)
        escritor.writerow(
            ["arquivo", "camada", "confianca", "revisao", "segundos", "campos_incertos",
             *[f"ok_{c}" for c in CAMPOS], "erro"]
        )  # fmt: skip
        for ln in linhas:
            d = ln.resultado.dados if ln.resultado else None
            escritor.writerow(
                [ln.arquivo, _categoria(ln, limiar), d.confianca if d else "",
                 d.requer_revisao_humana if d else "", f"{ln.segundos:.1f}",
                 ";".join(d.campos_incertos) if d else "",
                 *[ln.acertos.get(c, "") for c in CAMPOS], ln.erro or ""]
            )  # fmt: skip


if __name__ == "__main__":
    analisador = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    analisador.add_argument("--pasta", default="amostras", type=Path)
    analisador.add_argument(
        "--com-llm", action="store_true", help="roda o fallback LLM (gasta dinheiro)"
    )
    analisador.add_argument(
        "--textos", action="store_true", help="salva o texto do OCR em <pasta>/textos/"
    )
    argumentos = analisador.parse_args()
    sys.exit(avaliar(argumentos.pasta, argumentos.com_llm, argumentos.textos))
