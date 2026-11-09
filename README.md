# pi-patent-claims-extractor

Extrai o quadro reivindicatório de PDFs de patentes do INPI **com texto nativo**, usando
só PyMuPDF, sem OCR e sem GPU. Gera CSV com uma reivindicação por linha, para pesquisa e
para gerar embeddings (TAC: título, resumo e reivindicações).

> Ferramenta de pesquisa (Ibict). Não faz parte do pipeline de produção de XML ST.36.

## Instalação

```bash
pip install -r requirements.txt     # pymupdf
```

## Fluxo

| Etapa | Script | Saída |
|---|---|---|
| 1. Extração | `claims_extractor.py` | `<saida>.csv`, `<saida>_relatorio.csv`, `<saida>_pulados.csv` |
| 1b. Pastas grandes | `rodar_em_blocos.py` | `_parcial/` (retomável) |
| 2. Junção e deduplicação | `deduplicar_concessao.py` | CSV final + `<final>_dedup.csv` |
| 3. Título, resumo e IPC | `coletar_patentes_v4.py` | `patentes_v4_tai.csv`, `patentes_v4_nao_encontrados.csv` |

### 1. Extração

```bash
python claims_extractor.py <pasta_ou_pdf> -o reivindicacoes.csv
    [--formato-id completo|numero]   # BR1120180673331-A2 (padrão) | 112018067333
    [--por-documento]                # uma linha por pedido
    [--validar-xml]                  # compara com o XML ST.36 que estiver na pasta do PDF
    [--excel]                        # UTF-8 com BOM
```

- `reivindicacoes.csv` tem as colunas `pedido` e `reivindicacao`. O texto mantém a
  numeração do PDF ("1. Uma memória, compreendendo: ...").
- `reivindicacoes_relatorio.csv` traz um registro por PDF, com status, páginas, número
  de quadros, observações e, com `--validar-xml`, `n_xml` e `f1_xml`.
- `reivindicacoes_pulados.csv` lista só os PDFs que não geraram reivindicações:
  `sem_texto` (digitalizado), `sem_secao`, `sem_numeracao`, `pdf_vazio`, `erro`,
  `erro_abertura`.

O ID vem da linha (21)/(11) da capa: número, dígito verificador e kind (A2, B1, U2, Y1,
E2, F1). Ele só é aceito quando confere com o número do nome do arquivo; senão, usa-se
o nome do arquivo. Arquivos `._*` do macOS são ignorados.

### 1b. Pastas grandes

```bash
python rodar_em_blocos.py <pasta> <segundos>
```

Processa durante o tempo indicado e grava em `_parcial/` (reivindicações, relatório e
pulados, à medida que avança). Pode ser chamado de novo quantas vezes for preciso: cada
execução pula os PDFs já feitos.

### 2. Junção e deduplicação

```bash
python deduplicar_concessao.py final.csv extracao1.csv [extracao2.csv ...]
```

Cada extração precisa do `_relatorio.csv` ao lado. O script agrupa pelo número do pedido
(12 dígitos; 7 para PI/MU antigos), sem prefixo, DV nem kind. Quando o grupo tem
concessão (B*, Y*, F*, C*), fica só a concessão; senão, fica a publicação. PDFs
duplicados entre pastas entram uma vez. `final_dedup.csv` documenta cada decisão.

### 3. Título, resumo e IPC (Manticore)

```bash
python coletar_patentes_v4.py --host <servidor>     # ou MANTICORE_HOST=<servidor>
```

Lê `pedidos_final.csv` (`pedido`, `numero_base`, `prefixo`, `dv`, `kind`) e casa
`numero_base` com `application_number_norm` da tabela `patentes_v4`, via API HTTP
(porta 9308). Só faz SELECT e só usa a biblioteca padrão.

## Como a extração funciona

1. **Seção.** Procura uma página cujo topo tenha o título do quadro reivindicatório:
   REIVINDICAÇÕES, Reivindicações:, REIVINDICAÇÃO, "EMENDADAS", letras espaçadas e erros
   comuns (REINVINDICAÇÕES, REIVINDICAGÕES). Segue as páginas enquanto o contador próprio
   da seção (1/N, 2/N...) continua e para em RESUMO, DESENHOS etc. Sem título, aceita uma
   página 1/N que comece com "1.".
2. **Limpeza.** Remove o contador de página, o rodapé "Petição ..., pág. x/y", a
   numeração de linhas na margem (5, 10, 15...) e o ruído de carimbo/rubrica, e corrige
   "1 O." → "10." (camada de OCR na origem).
3. **Divisão.** Separa as reivindicações pela numeração crescente, tolerando saltos de
   até 3 números e "21 COMPOSTO" sem ponto. Quando falta um número, procura por ele no
   meio da reivindicação anterior.
4. **Hifenização.** Para cada palavra quebrada na linha, decide entre manter o hífen
   ("pré-carga") e retirá-lo ("especifi-cação" → "especificação") usando o vocabulário do
   próprio documento. Sem evidência no documento, aplica as regras ortográficas de
   prefixos e de ênclise.
5. Se houver mais de um quadro (ex.: um anexado ao pedido PCT e outro emendado), fica
   com o primeiro que tenha reivindicações numeradas.

Os padrões de título, rodapé e capa foram adaptados do pipeline
`IBICT-INPI/pi-patent-pdf-to-st36` (`pdf_text.py`, `pdf_kind.py`).

## Validação (nov/2026)

- **Lotes RPI 2505 (2019):** 299 XML ST.36 revisados por humanos. Contra 206 deles, o F1
  de palavras teve mediana de 0,9996 e média de 0,992, e a contagem de reivindicações
  bateu em 96% dos casos.
- **Bucket heterogêneo (5.431 PDFs: A2, B1, U2, Y1, E2, F1):** 94% extraídos, 2% sem
  texto, 1,6% sem seção. Contra uma extração anterior de ~100 documentos, o F1 mediano
  foi de 0,989.
- Desempenho: cerca de 0,25 s por PDF num MacBook Air.

## Limitações conhecidas

- PDFs digitalizados (sem camada de texto) não são processados. Eles aparecem em
  `_pulados.csv`.
- Fórmulas químicas e matemáticas e tabelas saem como texto achatado.
- B1 e layouts antigos de PI foram validados só por amostragem.
- Com mais de um quadro reivindicatório, a escolha do primeiro é uma regra, não uma
  detecção da versão vigente.

## Testes

```bash
python -m pytest -q        # ou: python tests/test_parsing.py
```

Os testes cobrem as heurísticas de texto (título, numeração, margem, OCR, hifenização,
capa/kind, número base), sem precisar de PDFs.
