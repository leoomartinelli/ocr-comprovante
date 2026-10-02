# Como ligar o OCR com a Edusef (guia simples)

## A ideia em uma figura

```
Pai manda o print        n8n baixa a imagem       OCR lê e devolve        n8n registra na Edusef
no WhatsApp          →   e manda para o OCR   →   o JSON com os dados →   como "EM ANÁLISE"
                                                                                ↓
                                                      Admin da escola confere e dá a baixa
                         n8n responde o pai  ←────────────────────────────────┘
```

**Regra de ouro:** cada peça faz uma coisa só.

| Peça | Faz | NÃO faz |
|---|---|---|
| **OCR** (este projeto) | Lê o print e devolve JSON | Não fala com a Edusef, não decide nada |
| **n8n** | Orquestra: recebe, chama o OCR, confere, chama a Edusef, responde o pai | Não lê imagem |
| **Edusef** | Guarda o comprovante "em análise" e deixa o admin dar a baixa | Não dá baixa sozinha |

## O que já está pronto

O OCR já funciona: `POST /ocr/comprovante`, header `X-API-Key`, campo `arquivo` com a imagem.
Veja o `LEIA-ME.md` para rodar e o formato do JSON.

## O que falta fazer

### Parte 1 — A Edusef precisa de 3 coisas

> **Não sei se a Edusef já tem alguma delas.** Veja primeiro se existe API (ou se dá para criar
> rotas) no repositório PHP. Abaixo está o mínimo de que o n8n precisa.

**1. Achar o responsável pelo número do WhatsApp**
`GET /api/responsaveis?telefone=5511999999999`
Devolve: `id`, `nome`, e os alunos dele. Se o número não está cadastrado, devolve vazio.

**2. Listar as parcelas em aberto dele**
`GET /api/responsaveis/{id}/parcelas?status=aberta`
Devolve, para cada parcela: `id`, `aluno`, `valor`, `vencimento`.

**3. Receber o comprovante "em análise"**
`POST /api/comprovantes` (multipart, porque leva a imagem)

| Campo | Exemplo | Para quê |
|---|---|---|
| `responsavel_id` | 123 | Quem mandou |
| `parcela_id` | 456 (ou vazio) | Parcela provável, se o n8n achou |
| `valor` | 100.00 | Valor lido |
| `data_hora` | 2026-09-28T22:29:14-03:00 | Quando pagou |
| `id_transacao` | E1234...J | Código Pix |
| `hash_arquivo` | 36069d48... | Para recusar o mesmo arquivo duas vezes |
| `favorecido_nome` | ESCOLA EXEMPLO | Para o admin conferir |
| `pagador_nome` | MARIA LUISA | Para o admin conferir |
| `requer_revisao` | true/false | Alerta para o admin |
| `observacao` | "Valor diferente da parcela" | Texto do n8n para o admin |
| `arquivo` | a imagem original | **Obrigatório**: o OCR não guarda o arquivo |

A Edusef grava com status **"em análise"**. Nunca dá baixa por conta própria.

**Importante:** o OCR **não guarda a imagem** (por causa da LGPD). Quem precisa guardar
para o admin conferir é a Edusef. Por isso o n8n envia a imagem original junto.

### Parte 2 — O fluxo no n8n (nó por nó)

1. **Gatilho do WhatsApp:** recebe a mensagem. Se não tem imagem, responde "mande o print do comprovante".
2. **Baixar a mídia:** pega a imagem da mensagem.
3. **HTTP Request → OCR**
   - POST `http://SERVIDOR_OCR:8000/ocr/comprovante`
   - Header `X-API-Key`: a chave do `.env` do OCR
   - Body: Form-Data, arquivo binário, nome `arquivo`
   - Timeout: 60 segundos
   - On Error: "Continue (using error output)"
4. **Se deu erro (400/422):** responde o pai "não consegui ler, mande outro print" e **para aqui**.
5. **HTTP Request → Edusef:** busca o responsável pelo telefone. Se não achou, avisa o pai e a secretaria e **para aqui**.
6. **HTTP Request → Edusef:** busca as parcelas em aberto.
7. **Nó Code: conferir.** Ver a tabela abaixo. O resultado é só um texto de `observacao`.
8. **HTTP Request → Edusef:** `POST /api/comprovantes` com os dados, a observação e a imagem.
9. **Responder o pai:** "Recebemos seu comprovante. A escola vai conferir e confirmar o pagamento."

### Como conferir (passo 7)

Compare em código simples, **sem espaços e sem acentos, em maiúsculas** (o OCR às vezes cola palavras):

| Confere | Se estiver certo | Se estiver errado |
|---|---|---|
| `valor` igual ao valor de alguma parcela em aberto | `parcela_id` = essa parcela | observação: "valor não bate com nenhuma parcela" |
| `favorecido.nome` parecido com o nome da escola | nada a fazer | observação: "favorecido diferente da escola" |
| `requer_revisao_humana` é `true` | nada a fazer | observação: "OCR com dúvida: " + `campos_incertos` |

**Em todos os casos o comprovante vai para "em análise".** A conferência só ajuda o admin, não decide.

### Duplicidade

O pai pode mandar o mesmo print duas vezes. A Edusef deve **recusar** se já existir um comprovante
com o mesmo `hash_arquivo`. Para o mesmo pagamento em prints diferentes, olhe também o `id_transacao`
(compare ignorando `0`/`O` e `1`/`l`/`I`, porque o OCR confunde essas letras).

## Checklist para colocar no ar

- [ ] Descobrir se a Edusef já tem API; se não, criar as 3 rotas da Parte 1
- [ ] Definir uma chave de API para o n8n falar com a Edusef
- [ ] Subir o OCR (`docker compose up -d --build`) num lugar que o n8n alcance
- [ ] Montar o fluxo no n8n (Parte 2)
- [ ] Testar com 5 a 10 prints reais antes de abrir para os pais
- [ ] Rodar `uv run python scripts/avaliar.py` com 20–30 comprovantes reais (veja o `LEIA-ME.md`)

## O que preciso saber para ajudar mais

1. **A Edusef tem API?** Qual framework (Laravel, CodeIgniter, PHP puro)? Como autentica?
2. **Qual provedor do WhatsApp** (Evolution API, Z-API, API oficial da Meta)?
3. **O n8n e o OCR vão rodar onde?** (mesma máquina, VPS, Docker?)
4. **O número do WhatsApp do pai está cadastrado na Edusef?** Em que campo?
