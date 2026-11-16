#!/usr/bin/env python3
"""
pipeline — orquestra as etapas do extrator e organiza as saídas em pastas.

Uso:
    python scripts/pipeline.py -i <pasta_de_lotes> -o <pasta_de_saida> [--excel] [--redo]
           [--manticore --host <servidor> [--porta 9308] [--tabela patentes_v4]]
    python scripts/pipeline.py -i <pasta_de_lotes> -o <pasta_de_saida> --only extract
    python scripts/pipeline.py -o <pasta_de_saida> --only consolidate [--host <servidor> ...]

Entrada (-i): uma pasta cujas subpastas de primeiro nível são os lotes (uma RPI cada, ex.:
samples/20180508, samples/20261020), com PDFs em qualquer nível abaixo delas. Se não houver
subpastas com PDFs, a própria pasta é tratada como um único lote, e o nome dela vira o nome
do lote. Em --only consolidate a entrada não é usada.

Etapas:
    1. extração      scripts/claims_extractor.py   (um por lote; grava a cada PDF e retoma)
    2. deduplicação  scripts/deduplicar_concessao.py (cruza todos os lotes)
    3. Manticore     scripts/coletar_patentes_v4.py  (opcional: só com --manticore/--host)

Sem --only, roda tudo para os lotes da entrada. Com --only, roda uma parte:
    --only extract      só a etapa 1, para os lotes da entrada. Serve para extrair um lote por vez
                        (ex.: baixar, extrair, apagar os PDFs e seguir para o próximo).
    --only consolidate  só as etapas 2 e 3, sobre TODAS as extrações que já estiverem em
                        <saida>/extracoes/ (os PDFs não são necessários). Roda uma vez, no final.

Saídas (em -o, que é criada se não existir):
    extracoes/<lote>/reivindicacoes.csv, _relatorio.csv, _pulados.csv
    dedup/<data>_<entrada>/final.csv, final_dedup.csv    (em --only consolidate: <data>_consolidado)
    tac/<data>_<entrada>/pedidos_final.csv, patentes_v4_tai.csv, patentes_v4_nao_encontrados.csv
    excel/...   cópias com BOM (UTF-8) para abrir no Excel (só com --excel)

Os CSVs de extracoes/ e dedup/ ficam sem BOM, que é o que as etapas seguintes leem.

Retomada: a extração grava a cada PDF. Se for interrompida, rodar o mesmo comando de novo
continua de onde parou, e os PDFs já extraídos são reaproveitados. Depois de mudar o código
do extrator (ou trocar PDFs), use --redo para descartar o que existe e extrair tudo de novo.
"""
from __future__ import annotations

import argparse
import csv
import os
import re
import sys
from datetime import date
from pathlib import Path

import claims_extractor as ce
import coletar_patentes_v4 as cp
import deduplicar_concessao as dd


def achar_lotes(entrada: Path) -> list[Path]:
    """Subpastas de primeiro nível com PDFs; sem elas, a própria pasta é o lote."""
    def tem_pdf(d: Path) -> bool:
        return any(not p.name.startswith("._") for p in d.rglob("*.pdf"))
    lotes = sorted(d for d in entrada.iterdir() if d.is_dir() and tem_pdf(d))
    return lotes or ([entrada] if tem_pdf(entrada) else [])


def extracoes_existentes(saida: Path) -> tuple[list[Path], list[Path]]:
    """CSVs de extração já gravados em <saida>/extracoes/<lote>/. Devolve (utilizáveis,
    sem relatório ao lado); o dedup precisa do relatório para saber o que foi extraído."""
    ok, sem_relatorio = [], []
    for c in sorted((saida / "extracoes").glob(f"*/{ce.ARQ_REIV}")):
        (ok if c.with_name(ce.ARQ_REL).exists() else sem_relatorio).append(c)
    return ok, sem_relatorio


def copia_excel(origem: Path, destino: Path) -> None:
    """Copia um CSV UTF-8 acrescentando o BOM, para o Excel reconhecer os acentos."""
    destino.parent.mkdir(parents=True, exist_ok=True)
    texto = origem.read_text(encoding="utf-8")
    destino.write_text(texto, encoding="utf-8-sig", newline="")


def gerar_pedidos_final(dedup_csv: Path, destino: Path) -> int:
    """Monta o pedidos_final.csv (pedido, numero_base, prefixo, dv, kind) a partir do
    final_dedup.csv, só com os documentos mantidos."""
    n = 0
    with open(dedup_csv, encoding="utf-8") as f, open(destino, "w", newline="", encoding="utf-8") as g:
        w = csv.writer(g)
        w.writerow(["pedido", "numero_base", "prefixo", "dv", "kind"])
        for r in csv.DictReader(f):
            if r["mantido"] != "sim":
                continue
            corpo = r["pedido"].split("-")[0]                    # BR1120120255134
            prefixo = re.match(r"[A-Za-z]*", corpo).group(0).upper()
            digitos = corpo[len(prefixo):]
            dv = digitos[len(r["numero_base"]):] if digitos.startswith(r["numero_base"]) else ""
            w.writerow([r["pedido"], r["numero_base"], prefixo, dv, r["kind"]])
            n += 1
    return n


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("-i", "--entrada", type=Path,
                    help="pasta de entrada, cujas subpastas são os lotes (uma RPI cada); "
                         "obrigatória, exceto com --only consolidate")
    ap.add_argument("-o", "--saida", type=Path, required=True,
                    help="pasta de saída (criada se não existir)")
    ap.add_argument("--only", choices=["extract", "consolidate"],
                    help="roda só uma parte: extract (etapa 1, para os lotes da entrada) ou consolidate "
                         "(etapas 2 e 3, sobre todas as extrações já existentes em <saida>/extracoes/)")
    ap.add_argument("--excel", action="store_true", help="gera também cópias com BOM em <saida>/excel/")
    ap.add_argument("--redo", action="store_true",
                    help="descarta as extrações existentes em <saida>/extracoes/ e refaz tudo "
                         "(sem ela, os PDFs já extraídos são reaproveitados)")
    ap.add_argument("--manticore", action="store_true",
                    help="inclui a etapa 3 (título, resumo e IPC no Manticore); requer --host")
    ap.add_argument("--host", help="servidor Manticore (ou variável MANTICORE_HOST); informá-lo já ativa a etapa 3")
    ap.add_argument("--porta", type=int, default=9308, help="porta HTTP do Manticore (padrão: 9308)")
    ap.add_argument("--tabela", default="patentes_v4", help="tabela do Manticore (padrão: patentes_v4)")
    args = ap.parse_args(argv)

    roda_extracao = args.only in (None, "extract")
    roda_consolidacao = args.only in (None, "consolidate")
    usar_manticore = args.manticore or args.host is not None
    host = args.host or os.environ.get("MANTICORE_HOST")
    if usar_manticore and not host:
        ap.error("a etapa do Manticore precisa do servidor: use --host <servidor> ou defina MANTICORE_HOST")
    if usar_manticore and not roda_consolidacao:
        ap.error("--only extract não roda a etapa do Manticore; use --only consolidate (ou omita --only)")
    if args.redo and not roda_extracao:
        ap.error("--redo refaz a extração e não combina com --only consolidate")
    if roda_extracao:
        if args.entrada is None:
            ap.error("-i/--entrada é obrigatório (só pode ser omitido com --only consolidate)")
        if not args.entrada.is_dir():
            ap.error(f"pasta de entrada não encontrada: {args.entrada}")
        lotes = achar_lotes(args.entrada)
        if not lotes:
            print("Nenhum PDF encontrado.", file=sys.stderr)
            return 1

    saida = args.saida
    nome = args.entrada.resolve().name if roda_extracao else "consolidado"
    rotulo = f"{date.today().isoformat()}_{nome}"
    total = int(roda_extracao) + (1 + int(usar_manticore) if roda_consolidacao else 0)

    # 1. extração, uma pasta por lote
    if roda_extracao:
        csvs = []
        for lote in lotes:
            pasta = saida / "extracoes" / lote.name
            print(f"\n=== [1/{total}] Extração do lote {lote.name} -> {pasta}", file=sys.stderr)
            ce.extrair_incremental(lote, pasta, redo=args.redo)
            csvs.append(pasta / ce.ARQ_REIV)
    else:
        csvs, sem_relatorio = extracoes_existentes(saida)
        for c in sem_relatorio:
            print(f"AVISO: {c.parent.name} ignorado (sem {ce.ARQ_REL} ao lado)", file=sys.stderr)
        if not csvs:
            print(f"Nenhuma extração encontrada em {saida / 'extracoes'}.", file=sys.stderr)
            return 1
        print(f"\n{len(csvs)} lotes extraídos em {saida / 'extracoes'} "
              f"({csvs[0].parent.name} ... {csvs[-1].parent.name})", file=sys.stderr)

    pasta_dedup = pasta_tac = None
    if roda_consolidacao:
        # 2. deduplicação cruzando os lotes
        pasta_dedup = saida / "dedup" / rotulo
        pasta_dedup.mkdir(parents=True, exist_ok=True)
        etapa = int(roda_extracao) + 1
        print(f"\n=== [{etapa}/{total}] Deduplicação -> {pasta_dedup}", file=sys.stderr)
        dd.deduplicar(pasta_dedup / "final.csv", csvs)

        # 3. Manticore (opcional)
        if usar_manticore:
            pasta_tac = saida / "tac" / rotulo
            pasta_tac.mkdir(parents=True, exist_ok=True)
            print(f"\n=== [{total}/{total}] Manticore ({host}:{args.porta}) -> {pasta_tac}", file=sys.stderr)
            n = gerar_pedidos_final(pasta_dedup / "final_dedup.csv", pasta_tac / "pedidos_final.csv")
            print(f"  pedidos_final.csv: {n} pedidos", file=sys.stderr)
            try:
                cp.coletar(host, args.porta, args.tabela, pasta_tac / "pedidos_final.csv", pasta_tac)
            except (OSError, RuntimeError) as e:     # URLError é OSError; RuntimeError vem do Manticore
                print(f"\nERRO na etapa do Manticore: {e}\n"
                      f"As etapas anteriores foram concluídas e estão em {saida}.", file=sys.stderr)
                return 2

    # cópias para o Excel
    if args.excel:
        for c in csvs:
            copia_excel(c, saida / "excel" / f"{c.parent.name}_reivindicacoes.csv")
            copia_excel(c.with_name(ce.ARQ_REL), saida / "excel" / f"{c.parent.name}_relatorio.csv")
        if pasta_dedup:
            copia_excel(pasta_dedup / "final.csv", saida / "excel" / f"{rotulo}_final.csv")

    if pasta_dedup:
        print(f"\nConcluído. Corpus final: {pasta_dedup / 'final.csv'}", file=sys.stderr)
        if pasta_tac:
            print(f"Título/resumo/IPC: {pasta_tac / 'patentes_v4_tai.csv'}", file=sys.stderr)
    else:
        print(f"\nExtração concluída em {saida / 'extracoes'}. "
              f"Para consolidar: --only consolidate -o {saida}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
