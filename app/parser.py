import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from app import validadores as val
from app.schemas import Favorecido, Pagador

BRT = timezone(timedelta(hours=-3))

# Palavra de nome com esta quantidade de letras (ou mais) provavelmente perdeu um espaço.
PALAVRA_COLADA_MIN = 12

# Lookalikes que o OCR confunde; só para comparar rótulos, nunca para valores.
_PARECIDOS = str.maketrans({"0": "o", "1": "i", "l": "i", "5": "s", "8": "b", "|": "i", "$": "s"})


def chave(texto: str) -> str:
    """Forma comparável de um rótulo: sem acento, minúscula, só letras, sem espaços.
    Robusta a espaços perdidos e a trocas como 0/o e l/i."""
    return re.sub(r"[^a-z]", "", val.sem_acento(texto).lower().translate(_PARECIDOS))


def _chaves(*rotulos: str) -> frozenset[str]:
    return frozenset(chave(r) for r in rotulos)


_NOME = _chaves("nome", "nome completo", "razão social")
_NOME_FAVORECIDO = _chaves(
    "nome do recebedor", "nome do favorecido", "nome do beneficiário", "nome do destinatário",
    "recebedor", "favorecido", "beneficiário", "destinatário",
)  # fmt: skip
_NOME_PAGADOR = _chaves("nome do pagador", "nome do remetente", "pagador", "remetente")
_DOCUMENTO = _chaves("cpf/cnpj", "cpf", "cnpj", "documento", "cpf ou cnpj")
_INSTITUICAO = _chaves(
    "instituição", "banco", "instituição financeira", "instituio"
)  # "instituio": OCR que perdeu ç e ã
_CHAVE_PIX = _chaves("chave pix", "chave", "chave do pix")
_IDENTIFICADOR = _chaves("identificador", "id do pagamento")
_TODOS_ROTULOS = (
    _NOME | _NOME_FAVORECIDO | _NOME_PAGADOR | _DOCUMENTO | _INSTITUICAO | _CHAVE_PIX
    | _IDENTIFICADOR | _chaves("tipo", "valor", "data", "número de controle", "agência", "conta")
)  # fmt: skip

_SECAO_FAVORECIDO = _chaves(
    "quem recebeu", "recebeu", "quem recebe", "recebedor", "favorecido", "destino",
    "destinatário", "beneficiário", "para quem", "para",
)  # fmt: skip
_SECAO_PAGADOR = _chaves(
    "quem pagou", "pagou", "quem paga", "pagador", "origem", "remetente", "quem enviou",
    "quem efetuou", "de",
)  # fmt: skip
_PREFIXO_DADOS = re.compile(r"^dados(dequem|de|do|da)?")
_NAO_E_CABECALHO = object()

_EXCLUIR_VALOR = tuple(
    chave(p)
    for p in ("tarifa", "saldo", "limite", "juros", "desconto", "multa", "abatimento", "iof",
              "encargo", "disponível")
)  # fmt: skip
_DATA_PRIORITARIA = tuple(
    chave(p)
    for p in ("data do débito", "data do pagamento", "data da transação", "data e hora",
              "realizado em", "efetivado em", "pago em")
)  # fmt: skip
_DATA_IGNORADA = tuple(chave(p) for p in ("vencimento", "agendamento", "nascimento", "validade"))

_MESES = {"jan": 1, "fev": 2, "mar": 3, "abr": 4, "mai": 5, "jun": 6,
          "jul": 7, "ago": 8, "set": 9, "out": 10, "nov": 11, "dez": 12}  # fmt: skip

_VALOR = re.compile(r"(?<![A-Za-z])R\s?[$S5]\s*-?\s*(\d[\d.,]*\d)")
_VALOR_BR = re.compile(r"(\d[\d.,]*?)[.,](\d{2})")
_HORA = r"(?:[\s,\-–]*(?:[aà]s\s*)?(\d{2})\s*:\s*(\d{2})(?:\s*:\s*(\d{2}))?)?"
_DATA_NUM = re.compile(r"(?<!\d)(\d{2})\s*/\s*(\d{2})\s*/\s*(\d{4})(?!\d)" + _HORA)
_DATA_TXT = re.compile(
    r"(?<!\d)(\d{1,2})\s*(?:de\s+)?([A-Za-zçÇ]{3,9})\.?\s*(?:de\s+)?(\d{4})(?!\d)" + _HORA,
    re.IGNORECASE,
)
_MASCARA = r"[\d*•]"
_CNPJ = re.compile(
    rf"(?<!{_MASCARA})({_MASCARA}{{2}})[.\s]?({_MASCARA}{{3}})[.\s]?({_MASCARA}{{3}})"
    rf"/?({_MASCARA}{{4}})[-\s]?({_MASCARA}{{2}})(?!{_MASCARA})"
)
_CPF = re.compile(
    rf"(?<!{_MASCARA})({_MASCARA}{{3}})[.\s]?({_MASCARA}{{3}})[.\s]?({_MASCARA}{{3}})"
    rf"[-\s]?({_MASCARA}{{2}})(?!{_MASCARA})"
)


@dataclass
class Extracao:
    valor: float | None = None
    data_hora: datetime | None = None
    id_transacao: str | None = None
    e2e_utc: datetime | None = None
    pagador: Pagador = field(default_factory=Pagador)
    favorecido: Favorecido = field(default_factory=Favorecido)
    banco: str | None = None
    identificador: str | None = None
    incertos: set[str] = field(default_factory=set)


# ---------- rótulos ----------


def _consumir(linha: str, quantidade: int) -> int | None:
    """Posição onde terminam `quantidade` letras (no formato de `chave`) do início da linha."""
    lidas = 0
    for pos, ch in enumerate(linha):
        lidas += len(chave(ch))
        if lidas >= quantidade:
            return pos + 1
    return None


def _apos_rotulo(linha: str, rotulos: frozenset[str]) -> str | None:
    """Valor depois do rótulo no início da linha ("" se só o rótulo), ou None se a linha
    não começa com nenhum dos rótulos. Funciona com ou sem ':' e com espaços perdidos."""
    for r in sorted(rotulos, key=len, reverse=True):
        pos = _consumir(linha, len(r))
        if pos is not None and chave(linha[:pos]) == r:
            return linha[pos:].lstrip(" :.-–\t").strip()
    return None


def _classificar_cabecalho(linha: str):
    if ":" in linha:
        return _NAO_E_CABECALHO
    c = chave(linha)
    if not c or len(c) > 30:
        return _NAO_E_CABECALHO
    base = _PREFIXO_DADOS.sub("", c)
    if base in _SECAO_FAVORECIDO:
        return "favorecido"
    if base in _SECAO_PAGADOR:
        return "pagador"
    if base != c:
        return None  # "Dados do pagamento": seção neutra
    return _NAO_E_CABECALHO


def _secoes(linhas: list[str]) -> list[str | None]:
    atual = None
    resultado = []
    for linha in linhas:
        novo = _classificar_cabecalho(linha)
        if novo is not _NAO_E_CABECALHO:
            atual = novo
        resultado.append(atual)
    return resultado


def _valor_rotulado(linhas: list[str], i: int, rotulos: frozenset[str]) -> str | None:
    valor = _apos_rotulo(linhas[i], rotulos)
    if valor is None:
        return None
    if not valor and i + 1 < len(linhas):
        proxima = linhas[i + 1]
        if _apos_rotulo(proxima, _TODOS_ROTULOS) is None and (
            _classificar_cabecalho(proxima) is _NAO_E_CABECALHO
        ):
            valor = proxima
    return valor or None


# ---------- valor ----------


def _para_float(texto: str) -> float | None:
    m = _VALOR_BR.fullmatch(texto)
    if not m:
        return None
    return float(f"{re.sub(r'[.,]', '', m.group(1))}.{m.group(2)}")


def todos_os_valores(linhas: list[str]) -> list[float]:
    achados = []
    for linha in linhas:
        if any(p in chave(linha) for p in _EXCLUIR_VALOR):
            continue
        for m in _VALOR.finditer(linha):
            numero = _para_float(m.group(1))
            if numero:
                achados.append(numero)
    return achados


def _extrair_valor(linhas: list[str], e: Extracao) -> None:
    contagem = Counter(todos_os_valores(linhas))
    if not contagem:
        return
    e.valor = contagem.most_common(1)[0][0]  # empate: o que aparece primeiro (destaque)
    if len(contagem) > 1:
        e.incertos.add("valor")


# ---------- data ----------


def _montar_data(dia, mes, ano, hora, minuto, segundo) -> tuple[datetime, bool] | None:
    agora = datetime.now(BRT)
    try:
        data = datetime(
            int(ano), int(mes), int(dia), int(hora or 0), int(minuto or 0), int(segundo or 0),
            tzinfo=BRT,
        )  # fmt: skip
    except ValueError:
        return None
    if not val.ANO_MINIMO_PIX <= data.year <= agora.year + 1 or data > agora + timedelta(days=1):
        return None
    return data, hora is not None


def datas_candidatas(linhas: list[str]) -> list[tuple[int, datetime, bool, int]]:
    """(linha, data, tem_hora, prioridade); prioridade menor = mais confiável."""
    achadas = []
    for i, linha in enumerate(linhas):
        encontradas = [(m, m.groups()) for m in _DATA_NUM.finditer(linha)]
        for m in _DATA_TXT.finditer(linha):
            mes = _MESES.get(val.sem_acento(m.group(2)).lower()[:3])
            if mes:
                dia, _, ano, *hora = m.groups()
                encontradas.append((m, (dia, mes, ano, *hora)))
        for m, (dia, mes, ano, hora, minuto, segundo) in encontradas:
            montada = _montar_data(dia, mes, ano, hora, minuto, segundo)
            if not montada:
                continue
            rotulo = chave(linha[: m.start()]) or (chave(linhas[i - 1]) if i else "")
            if any(p in rotulo for p in _DATA_IGNORADA):
                continue
            if any(p in rotulo for p in _DATA_PRIORITARIA):
                prioridade = 0
            elif "data" in rotulo or "hora" in rotulo:
                prioridade = 1
            else:
                prioridade = 2
            achadas.append((i, montada[0], montada[1], prioridade))
    return achadas


def _extrair_data(linhas: list[str], e: Extracao) -> None:
    candidatas = datas_candidatas(linhas)
    if not candidatas:
        return
    _, data, tem_hora, _ = min(candidatas, key=lambda c: (c[3], c[0]))
    e.data_hora = data
    if not tem_hora:
        e.incertos.add("data_hora")


# ---------- E2E ----------


def _tokens(linha: str) -> list[str]:
    return re.findall(r"[A-Za-z0-9]+", linha)


def _tem_digito(texto: str) -> bool:
    return any(c.isdigit() for c in texto)


def _candidatos_e2e(linhas: list[str]):
    """Candidatos de exatamente 32 caracteres. Junta pedaços só quando o E2E foi partido:
    na mesma linha (espaço no meio) ou quebrado no fim da linha, com o resto sozinho na
    linha seguinte. Nunca completa um E2E cortado com palavras vizinhas."""
    for i, linha in enumerate(linhas):
        tokens = _tokens(linha)
        for j, token in enumerate(tokens):
            if len(token) == 32:
                yield token
            elif len(token) > 32:
                yield token[-32:]  # rótulo colado no início ("controleE607...")
            elif token[0] in "Ee" and len(token) >= 12:
                junto = token
                for seguinte in tokens[j + 1 : j + 3]:
                    if not _tem_digito(seguinte):
                        break  # pedaço de E2E quase sempre tem dígito; palavra comum não
                    junto += seguinte
                    if len(junto) == 32:
                        yield junto
                if j == len(tokens) - 1 and i + 1 < len(linhas):
                    resto = _tokens(linhas[i + 1])
                    if (
                        len(resto) == 1
                        and _tem_digito(resto[0])
                        and len(token) + len(resto[0]) == 32
                    ):
                        yield token + resto[0]


def _extrair_e2e(linhas: list[str], e: Extracao) -> None:
    for candidato in _candidatos_e2e(linhas):
        corrigido = val.corrigir_e2e(candidato)
        if corrigido:
            e.id_transacao = corrigido
            e.e2e_utc = val.data_do_e2e(corrigido)
            return


# ---------- partes ----------


def _limpar_nome(texto: str) -> str | None:
    nome = re.sub(r"\s+", " ", texto).strip(" :-–.,")
    if len(nome) < 3 or sum(c.isdigit() for c in nome) > 2 or "@" in nome:
        return None
    return nome


def _tem_palavra_colada(nome: str) -> bool:
    return any(len(p) >= PALAVRA_COLADA_MIN for p in re.findall(r"[A-Za-zÀ-ÿ]+", nome))


def achar_documento(texto: str) -> tuple[str, bool] | None:
    """(documento, confiável). Mascarado vira confiável (não há o que validar);
    completo com dígito verificador errado vira não confiável."""
    for padrao, tamanho in ((_CNPJ, 14), (_CPF, 11)):
        m = padrao.search(texto)
        if not m:
            continue
        grupos = [g.replace("•", "*") for g in m.groups()]
        bruto = "".join(grupos)
        if tamanho == 14:
            formatado = f"{grupos[0]}.{grupos[1]}.{grupos[2]}/{grupos[3]}-{grupos[4]}"
            valido = val.validar_cnpj
        else:
            formatado = f"{grupos[0]}.{grupos[1]}.{grupos[2]}-{grupos[3]}"
            valido = val.validar_cpf
        if "*" in bruto:
            return formatado, True
        return formatado, valido(bruto)
    return None


def _extrair_partes(linhas: list[str], e: Extracao) -> None:
    secoes = _secoes(linhas)
    partes = (("favorecido", e.favorecido, _NOME_FAVORECIDO), ("pagador", e.pagador, _NOME_PAGADOR))

    # 1) nome com rótulo explícito ("Nome do recebedor", "Pagador"), em qualquer lugar
    for i in range(len(linhas)):
        for _, alvo, rotulos in partes:
            if alvo.nome:
                continue
            valor = _valor_rotulado(linhas, i, rotulos)
            if valor:
                alvo.nome = _limpar_nome(valor)

    # 2) demais campos, dentro da seção de cada parte
    instituicoes: dict[str, str] = {}
    for nome_secao, alvo, _ in partes:
        for i in (i for i, s in enumerate(secoes) if s == nome_secao):
            if not alvo.nome:
                valor = _valor_rotulado(linhas, i, _NOME)
                alvo.nome = _limpar_nome(valor) if valor else None
            if not alvo.documento:
                valor = _valor_rotulado(linhas, i, _DOCUMENTO)
                achado = achar_documento(valor) if valor else None
                if achado:
                    alvo.documento = achado[0]
                    if not achado[1]:
                        e.incertos.add(f"{nome_secao}.documento")
            if nome_secao not in instituicoes:
                valor = _valor_rotulado(linhas, i, _INSTITUICAO)
                if valor:
                    instituicoes[nome_secao] = valor
            if nome_secao == "favorecido" and not e.favorecido.chave_pix:
                valor = _valor_rotulado(linhas, i, _CHAVE_PIX)
                if valor:
                    e.favorecido.chave_pix = valor
        if alvo.nome and _tem_palavra_colada(alvo.nome):
            e.incertos.add(f"{nome_secao}.nome")

    e.banco = instituicoes.get("favorecido") or instituicoes.get("pagador")


# ---------- identificador ----------


def _corrigir_identificador(token: str) -> str:
    """Em identificador todo em maiúsculas, 'l' ou 'i' minúsculo é quase sempre um 'I' mal lido."""
    minusculas = [c for c in token if c.islower()]
    maiusculas = [c for c in token if c.isupper()]
    if len(maiusculas) >= 2 and all(c in "li" for c in minusculas):
        return token.replace("l", "I").replace("i", "I")
    return token


def _extrair_identificador(linhas: list[str], e: Extracao) -> None:
    for i in range(len(linhas)):
        valor = _valor_rotulado(linhas, i, _IDENTIFICADOR)
        m = re.match(r"[A-Za-z0-9._\-]{2,40}", valor or "")
        if m:
            e.identificador = _corrigir_identificador(m.group(0))
            return


def interpretar(texto: str) -> Extracao:
    linhas = [ln.strip() for ln in texto.splitlines() if ln.strip()]
    e = Extracao()
    _extrair_valor(linhas, e)
    _extrair_data(linhas, e)
    _extrair_e2e(linhas, e)
    _extrair_partes(linhas, e)
    _extrair_identificador(linhas, e)
    return e


__all__ = ["BRT", "Extracao", "chave", "datas_candidatas", "interpretar", "todos_os_valores"]
