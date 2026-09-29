# OCR de comprovantes Pix — relatório e guia rápido

Serviço FastAPI que recebe **um print** de comprovante Pix e devolve os dados em JSON.
Quem decide o que fazer com o resultado (conferir parcela, falar com a Edusef, responder o pai)
é o **n8n**. Este serviço só lê a imagem. PDF foi removido: agora recebe **só JPG, PNG ou WEBP**.

## Como ele lê um print (camadas, da mais barata para a mais cara)

1. **Cache**: o mesmo arquivo (SHA-256) já processado volta na hora (~0,2 s), sem OCR.
2. **Pré-processamento**: corrige rotação (EXIF), tons de cinza, inverte modo escuro, amplia 2x se a
   largura for menor que 800 px e divide prints muito compridos em faixas com sobreposição.
3. **OCR local** (RapidOCR, roda em CPU, sem custo por uso).
4. **Parser** por regras: valor, data/hora, E2E (com validação de formato e da data embutida),
   CPF/CNPJ (com dígito verificador quando completo), favorecido, pagador, banco, identificador.
5. **Confiança (0 a 1)**: valor + E2E válido + data coerente = 0,75 = "alta", sem LLM.
6. **Releitura local**: se a confiança ficou baixa ou há campo crítico duvidoso, lê de novo com a
   imagem ampliada (até 3 leituras). Custa só CPU.
7. **Fallback LLM (Claude Haiku 4.5)**: **desligado por padrão**. Só é chamado se, depois de tudo
   isso, a confiança ainda estiver abaixo de 0,75. Recebe só o **texto** já extraído (nunca a imagem).

## O que você precisa fazer / configurar

- [ ] **1. Criar o `.env`**: copie `.env.example` para `.env` e troque o `API_KEY` por uma chave longa
  (gere com `uv run python -c "import secrets; print(secrets.token_urlsafe(32))"`).
  Essa mesma chave vai no n8n, no header `X-API-Key`.
- [ ] **2. Rodar e testar** (seção abaixo).
- [ ] **3. Juntar 20–30 comprovantes reais anonimizados** (inclua prints ruins de propósito) na pasta
  `amostras/`, criar o `gabarito.csv` e rodar o script de avaliação. **É isso que diz a acurácia
  real.** Os números que tenho são só de prints sintéticos.
- [ ] **4. Montar o fluxo no n8n** (seção abaixo).
- [ ] **5. Subir com Docker** (`docker compose up -d --build`). **Não consegui testar o build**:
  o Docker Desktop abriu aqui mas o motor não subiu (provavelmente pedindo uma ação sua na janela).
- [ ] **6. Só depois dos números do passo 3**, decidir se liga o fallback LLM (seção final).

## Rodar e testar

```bash
# servidor local (a primeira chamada leva alguns segundos; o modelo carrega na subida)
uv run uvicorn app.main:app --port 8000
```

Numa segunda janela, com um print seu (troque a chave e o caminho):

```bash
curl -H "X-API-Key: SUA_CHAVE" -F "arquivo=@C:/caminho/print.png" http://localhost:8000/ocr/comprovante
```

Testes automáticos:

```bash
uv run pytest -m "not ocr"   # 152 testes, ~2 s, não usa o OCR real
uv run pytest                # 157 testes, ~45 s, inclui 5 com o OCR real em prints sintéticos difíceis
```

Ensaio do script de avaliação com prints **sintéticos** (dados fictícios, só para ver o fluxo):

```bash
uv run python scripts/gerar_amostras_demo.py
uv run python scripts/avaliar.py --pasta amostras_demo
```

### Avaliação com os seus comprovantes reais

Coloque as imagens em `amostras/` e crie `amostras/gabarito.csv` (vírgula ou ponto e vírgula):

```csv
arquivo,valor,id_transacao,data,favorecido
print1.jpg,100.00,E12345678202609290129Ab3dE9fGh1J,28/09/2026,NOME DO FAVORECIDO
```

Célula vazia = não avaliar aquele campo naquele arquivo. Depois:

```bash
uv run python scripts/avaliar.py --textos
```

`--textos` salva o texto que o OCR leu em `amostras/textos/` (útil para ver por que um print errou).
A pasta `amostras/` está no `.gitignore` (dado pessoal).

O script imprime: acurácia por campo, por camada, **% que precisaria de LLM** e
**"confiantes e erradas"**, o número mais importante: resultados que **não** pediram revisão mas
têm campo crítico errado. Meta: zero. Se aparecerem, me mande o arquivo `resultado.csv`.

## Contrato da resposta

`POST /ocr/comprovante`, multipart, campo `arquivo`, header `X-API-Key`.

| Campo | Significado |
|---|---|
| `valor`, `data_hora`, `id_transacao` | `null` se não achou. Nunca é inventado. |
| `data_hora` | Fuso `-03:00`. Vem da linha "Data do débito" quando existe. |
| `favorecido` / `pagador` | `nome`, `documento` (pode vir mascarado `***.123.456-**`); favorecido tem `chave_pix`. |
| `banco` | **Instituição do favorecido** (decisão minha: dá para conferir com a conta da escola). |
| `identificador` | Texto tipo `COIN999`. Opcional; não pesa na confiança. |
| `confianca` | 0 a 1. |
| `campos_incertos` | Campos críticos ausentes **ou** encontrados com dúvida. |
| `requer_revisao_humana` | `true` se confiança < 0,75 **ou** qualquer campo crítico (valor, E2E, data, nome do favorecido) ausente/duvidoso. |
| `metodo` | `ocr_local`, `ocr_local+llm` ou `cache` (nesse caso `metodo_original` diz qual camada resolveu). |
| `hash_arquivo` | SHA-256 do arquivo. |
| `texto_bruto` | Texto que o OCR leu (vem `null` quando a resposta é do cache). Não é gravado em log. |

Erros: `{"success": false, "message": "..."}` com **400** (arquivo inválido/vazio, PDF, sem campo
`arquivo`), **401** (chave), **413** (maior que 10 MB), **422** (imagem ilegível/sem texto).

## Configurando o n8n (nó HTTP Request)

- **Method:** POST · **URL:** `http://SERVIDOR:8000/ocr/comprovante`
- **Headers:** `X-API-Key` = a chave do `.env`
- **Body:** Form-Data → parâmetro do tipo **n8n Binary File**, nome **`arquivo`**, campo binário `data`
  (o arquivo baixado do WhatsApp)
- **Options → Timeout:** **60000** ms (o OCR leva de 3 a 12 s por print em CPU)
- **Settings → On Error:** "Continue (using error output)" para tratar os 422 (pedir novo print ao pai)

Sugestão de decisão depois da resposta:

1. `success = false` → responder "não consegui ler, mande outro print".
2. Comparar `valor` e o nome do favorecido com a parcela. **Compare o nome sem espaços e sem acentos**
   (o OCR às vezes cola palavras: `CARLOSMENDES`).
3. Registrar na Edusef sempre como **"em análise"**. Use `requer_revisao_humana` e `campos_incertos`
   como observação para o admin.
4. Duplicidade: use `hash_arquivo` (arquivo idêntico) e o `id_transacao` **normalizado**
   (veja a limitação 3 abaixo).

## Limitações que você precisa saber

1. **A acurácia real ainda é desconhecida.** Nas 9 amostras sintéticas: valor 100%, data 100%,
   favorecido 100%, E2E 89% (8/9), 0 "confiantes e erradas", 0% precisariam de LLM. Isso **não** vale
   para os seus prints. Só vi o layout real do Bradesco (o PDF que você mandou, que o parser leu
   certo); Nubank, Itaú, Inter, Santander, C6, Caixa e BB não foram testados com prints reais.
2. **Espaços perdidos nos nomes.** O OCR às vezes cola palavras. O serviço sinaliza quando uma
   palavra tem 12+ letras (`campos_incertos` + revisão). Colagem curta (`JOAOPEDRO`) **não** é detectada.
   Acentos são removidos (`ANDRÉ` vira `ANDRE`), sempre.
3. **E2E: risco de `0`/`O` e `1`/`l`/`I` nos últimos 11 caracteres.** Essa parte do E2E não tem como
   ser validada, então um E2E lido com uma dessas trocas pode sair **errado com confiança alta**
   (aconteceu numa amostra sintética, onde só o nome sinalizado salvou). Por isso: não use o E2E como
   única prova de pagamento nem como única chave de duplicidade sem normalizar (0=O, 1=l=I).
4. **Só Pix de pagamento** (E2E começa com `E`). Devolução (`D`), boleto, cartão e agendamento não.
5. **Lento em CPU:** 3–12 s por print (até 3 leituras quando o print é ruim). Limite de 2 OCRs
   simultâneos (`OCR_CONCORRENCIA`).
6. **LGPD:** o arquivo original **nunca** é gravado. O cache guarda hash + resultado (nomes e CPF
   parcial) por **30 dias** (`CACHE_TTL_DIAS`; `CACHE_ATIVO=false` desliga). O `texto_bruto` não vai
   para o cache nem para o log. O log guarda só os 12 primeiros caracteres do hash, a camada e a confiança.
7. **Dockerfile não testado** (Docker Desktop sem motor). Se o build falhar, me mande a mensagem.

## Fallback LLM (camada 7): como ligar quando fizer sentido

Está **implementado e testado com um cliente falso**. **Nunca chamou a API real** (não há chave aqui),
e a taxa de acionamento real depende dos seus prints.

Como o LLM é usado com segurança: só é chamado com confiança < 0,75, recebe só o texto, e só pode
**preencher campo vazio ou duvidoso** com valor que **também aparece no texto do OCR** (pode separar
palavras coladas, não pode inventar). Sem chave ou com erro de rede, segue com o resultado local.

Para testar com custo controlado (roda o LLM só nos arquivos que ficaram abaixo do limiar):

```bash
# no .env: ANTHROPIC_API_KEY=sk-ant-...
uv run python scripts/avaliar.py --com-llm
```

Ele mostra a taxa de acionamento, a acurácia antes/depois, os tokens e o custo estimado.
Se os números compensarem, ligue no `.env`: `LLM_FALLBACK_ATIVO=true`.

## Decisões que tomei sem perguntar

- `banco` = instituição do favorecido. · PDF → 400. · Arquivo grande → 413 (você não tinha listado).
- Limiar de confiança 0,75, ajustável em `CONFIANCA_MINIMA`. Pesos em `app/pontuacao.py`.
- Data coerente com o E2E = diferença de até 1 dia (o E2E é em UTC, o comprovante em `-03:00`).
- Python 3.12 fixado (o `rapidocr-onnxruntime` exige Python menor que 3.13).
- Nome com palavra de 12+ letras é tratado como suspeito. Pode marcar por engano um sobrenome
  longo legítimo (ex.: `VASCONCELLOS`); o efeito é só pedir revisão.

## Mapa do código

```
app/main.py           endpoint, API key, tratamento de erros
app/servico.py        orquestra as camadas (cache → OCR → releitura → LLM)
app/preprocessamento.py, leitura.py, motor_ocr.py   imagem → texto (motor trocável)
app/parser.py         texto → campos       app/validadores.py  CPF/CNPJ/E2E
app/pontuacao.py      confiança            app/cache.py        SQLite por hash
app/llm.py            fallback Claude Haiku
scripts/avaliar.py    avaliação com gabarito · gerar_amostras_demo.py · sintetico.py
tests/                157 testes
```
