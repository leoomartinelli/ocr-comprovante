"""Textos de OCR fictícios, no formato que o motor devolve (inclusive com defeitos reais)."""

E2E = "E12345678202609290129Ab3dE9fGh1J"

ESTILO_A_LIMPO = f"""Comprovante de pagamento Pix Valor: R$ 100,00
28/09/2026 - 22:29:17
Dados de quem recebeu
Nome: CARLOS MENDES DE ALBUQUERQUE
CPF/CNPJ: ***.123.456-**
Instituicao: BCO EXEMPLO S.A.
Dados do pagamento
Tipo: Pix
Valor: R$ 100,00
Identificador: COIN999
Data do debito: 28/09/2026 - 22:29:14
Numero de controle: {E2E}
Dados de quem pagou
Nome: MARIA LUISA DA SILVA
CPF: ***.654.321-**"""

# Saída real do RapidOCR a 1x: espaços perdidos, "Numer0", "ldentificador"
ESTILO_A_DEGRADADO = f"""Comprovante de pagamento Pix
Valor: R$ 100,00
28/09/2026-22:29:17
Dadosdequemrecebeu
Nome:CARLOSMENDESDEALBUQUERQUE
CPF/CNPJ: ***.123.456-**
Instituicao: BCO EXEMPLO S.A.
Dados do pagamento
ldentificador:COlN999
Datadodebito:28/09/2026-22:29:14
Numer0 de controle: {E2E}
Dados de quem pagou
Nome: MARIA LUiS DA SILVA
CPF: ***.654.321-**"""

ESTILO_B = f"""Transferência enviada
R$ 1.500,00
28 SET 2026 - 22:29:14
Destino
Nome
CARLOS MENDES DE ALBUQUERQUE
CPF
***.123.456-**
Instituição
PAGAMENTOS EXEMPLO S.A.
Chave Pix
carlos@exemplo.com.br
Origem
Nome
MARIA LUISA DA SILVA
ID da transação
{E2E}"""
