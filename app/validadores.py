import re
import unicodedata
from datetime import UTC, datetime

# Trocas típicas do OCR em posições onde só pode haver dígito.
_LETRA_PARA_DIGITO = str.maketrans(
    {"O": "0", "o": "0", "l": "1", "I": "1", "i": "1", "|": "1", "S": "5", "s": "5", "B": "8"}
)

ANO_MINIMO_PIX = 2020


def sem_acento(texto: str) -> str:
    return unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode()


def normalizar_nome(texto: str) -> str:
    """Só letras e dígitos, maiúsculo, sem acento: compara nomes mesmo com espaços perdidos."""
    return re.sub(r"[^A-Z0-9]", "", sem_acento(texto).upper())


def _digitos_verificadores(base: list[int], pesos: list[int]) -> int:
    resto = sum(d * p for d, p in zip(base, pesos, strict=True)) % 11
    return 0 if resto < 2 else 11 - resto


def validar_cpf(digitos: str) -> bool:
    if len(digitos) != 11 or not digitos.isdigit() or len(set(digitos)) == 1:
        return False
    nums = [int(c) for c in digitos]
    d1 = _digitos_verificadores(nums[:9], list(range(10, 1, -1)))
    d2 = _digitos_verificadores(nums[:10], list(range(11, 1, -1)))
    return nums[9] == d1 and nums[10] == d2


def validar_cnpj(digitos: str) -> bool:
    if len(digitos) != 14 or not digitos.isdigit() or len(set(digitos)) == 1:
        return False
    nums = [int(c) for c in digitos]
    d1 = _digitos_verificadores(nums[:12], [5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2])
    d2 = _digitos_verificadores(nums[:13], [6, 5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2])
    return nums[12] == d1 and nums[13] == d2


def data_do_e2e(e2e: str) -> datetime | None:
    """Valida o formato do E2E Pix (E + ISPB 8 + aaaammddhhmm 12 + 11 alfanuméricos) e
    devolve a data embutida, que é UTC."""
    if len(e2e) != 32 or e2e[0] != "E" or not e2e[1:21].isdigit() or not e2e[21:].isalnum():
        return None
    try:
        data = datetime.strptime(e2e[9:21], "%Y%m%d%H%M").replace(tzinfo=UTC)
    except ValueError:
        return None
    if not ANO_MINIMO_PIX <= data.year <= datetime.now(UTC).year + 1:
        return None
    return data


def corrigir_e2e(candidato: str) -> str | None:
    """Tenta validar o candidato como está; se falhar, troca letras parecidas com dígitos
    (O→0, l/I→1, S→5, B→8) nas posições que só aceitam dígito."""
    if len(candidato) != 32 or candidato[0].upper() != "E":
        return None
    corrigido = "E" + candidato[1:21].translate(_LETRA_PARA_DIGITO) + candidato[21:]
    return corrigido if data_do_e2e(corrigido) else None
