#!/usr/bin/env python3
"""
exportar_revisao — junta, num CSV por RPI, tudo o que a revisão humana precisa de cada pedido.

Uso:
    python scripts/exportar_revisao.py -o <pasta_de_saida> [--dedup <pasta>] [--tac <pasta>]

Roda DEPOIS da consolidação (pipeline.py --only consolidate), sobre a mesma pasta de saída:
    extracoes/<lote>/        relatório de cada PDF (status, páginas, obs)
    dedup/<data>_.../        final.csv + final_dedup.csv (quais pedidos ficam)
    tac/<data>_.../          patentes_v4_tai.csv do Manticore (opcional)
Por padrão usa a consolidação mais recente (nome em ordem alfabética) e, se existir, a pasta tac
de mesmo nome. Sem tac, as colunas do Manticore saem vazias e dá para gerar de novo depois.

Saída: <pasta_de_saida>/revisao/<AAAAMMDD>.csv, um por RPI (a data do lote; lotes de pastas de
ano diferentes com a mesma data caem no mesmo arquivo). Uma linha por pedido, coerente com a
deduplicação: cada pedido aparece uma só vez, na RPI do documento mantido. Entram os pedidos com
reivindicações extraídas (ok e ok_lacunas); os sem reivindicações não entram.

Colunas: rpi, lote, pedido, numero_base, kind, prefixo, dv, reivindicacoes (uma por linha, cada
uma começando pelo seu número), n_reivindicacoes, arquivo, status_extracao, paginas, n_secoes, obs,
e do Manticore: application_number, title, abstract, ipc_txt, ipc_json, first_subclass, nature,
status_patente, n_registros.
"""
from __future__ import annotations

import argparse
import csv
import re
import sys
from collections import defaultdict
from pathlib import Path

import claims_extractor as ce
import deduplicar_concessao as dd

csv.field_size_limit(2**31 - 1)

COLUNAS_MANTICORE = ["application_number", "title", "abstract", "ipc_txt", "ipc_json",
                     "first_subclass", "nature", "status_patente", "n_registros"]
COLUNAS = ["rpi", "lote", "pedido", "numero_base", "kind", "prefixo", "dv", "reivindicacoes",
           "n_reivindicacoes", "arquivo", "status_extracao", "paginas", "n_secoes", "obs"] + COLUNAS_MANTICORE


def rpi_do_lote(lote: str) -> str:
    """Data (AAAAMMDD) contida no nome do lote: 2021_20211109 -> 20211109. Sem data, o próprio nome."""
    m = re.search(r"(?<!\d)(\d{8})(?!\d)", lote)
    return m.group(1) if m else lote


def _ler(caminho: Path):
    with open(caminho, encoding="utf-8", newline="") as f:
        yield from csv.DictReader(f)


def exportar(saida: Path, dedup_dir: Path | None = None, tac_dir: Path | None = None) -> dict[str, int]:
    """Grava <saida>/revisao/<rpi>.csv. Devolve {rpi: nº de pedidos}."""
    if dedup_dir is None:
        candidatas = sorted(p for p in (saida / "dedup").glob("*") if (p / "final_dedup.csv").exists())
        if not candidatas:
            raise FileNotFoundError(f"nenhuma consolidação em {saida / 'dedup'} (rode --only consolidate)")
        dedup_dir = candidatas[-1]
    if tac_dir is None and (saida / "tac" / dedup_dir.name / "patentes_v4_tai.csv").exists():
        tac_dir = saida / "tac" / dedup_dir.name

    # pedidos mantidos pela deduplicação
    mantidos = {}
    for r in _ler(dedup_dir / "final_dedup.csv"):
        if r["mantido"] == "sim":
            mantidos[r["pedido"]] = {"numero_base": r["numero_base"], "kind": r["kind"],
                                     "lote": r["origem"].split("/")[0]}

    # relatório dos PDFs: linha do documento mantido e documentos do mesmo pedido que falharam
    bases = {m["numero_base"] for m in mantidos.values()}
    rel, falhas = {}, defaultdict(list)
    for caminho in sorted((saida / "extracoes").glob(f"*/{ce.ARQ_REL}")):
        lote = caminho.parent.name
        for r in _ler(caminho):
            if r["status"].startswith("ok"):
                if r["pedido"] in mantidos and mantidos[r["pedido"]]["lote"] == lote:
                    rel[r["pedido"]] = r
            else:
                base = dd.numero_base(r["arquivo"])
                if base in bases:
                    falhas[base].append((r["pedido"], r["status"]))

    # reivindicações dos pedidos mantidos
    reivs = defaultdict(list)
    for r in _ler(dedup_dir / "final.csv"):
        reivs[r["pedido"]].append(r["reivindicacao"])

    # Manticore (opcional)
    tai = {}
    if tac_dir is not None:
        for r in _ler(tac_dir / "patentes_v4_tai.csv"):
            tai[r["pedido"]] = r

    por_rpi = defaultdict(list)
    divergentes = 0
    for pedido in sorted(mantidos):
        m, lote = mantidos[pedido], mantidos[pedido]["lote"]
        r = rel.get(pedido)
        if r is None:                       # a deduplicação diz que está ok, mas o relatório não tem a linha
            print(f"AVISO: {pedido} sem linha no relatório de {lote}; ignorado", file=sys.stderr)
            continue
        if int(r["n_reivindicacoes"]) != len(reivs[pedido]):
            divergentes += 1
        obs = [r["obs"]] if r["obs"] else []
        outras = [f"{p} ({s})" for p, s in falhas.get(m["numero_base"], []) if p != pedido]
        if outras:
            obs.append("outra versão do pedido sem reivindicações extraídas: " + ", ".join(outras))
        prefixo, dv = dd.partes_pedido(pedido, m["numero_base"])
        linha = {"rpi": rpi_do_lote(lote), "lote": lote, "pedido": pedido, "numero_base": m["numero_base"],
                 "kind": m["kind"], "prefixo": prefixo, "dv": dv,
                 "reivindicacoes": "\n".join(reivs[pedido]), "n_reivindicacoes": r["n_reivindicacoes"],
                 "arquivo": r["arquivo"], "status_extracao": r["status"], "paginas": r["paginas"],
                 "n_secoes": r["n_secoes"], "obs": "; ".join(obs)}
        t = tai.get(pedido)
        for c in COLUNAS_MANTICORE:
            origem = "status" if c == "status_patente" else c
            linha[c] = t[origem] if t else ""
        por_rpi[linha["rpi"]].append(linha)

    destino = saida / "revisao"
    destino.mkdir(parents=True, exist_ok=True)
    for rpi, linhas in por_rpi.items():
        with open(destino / f"{rpi}.csv", "w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=COLUNAS)
            w.writeheader()
            w.writerows(linhas)

    total = sum(len(v) for v in por_rpi.values())
    print(f"{total} pedidos em {len(por_rpi)} RPIs -> {destino}", file=sys.stderr)
    if tac_dir is not None:
        com_dados = sum(1 for v in por_rpi.values() for l in v if l["pedido"] in tai)
        manticore = f"{com_dados}/{total} pedidos com dados do Manticore"
    else:
        manticore = "sem Manticore (colunas vazias)"
    print(f"consolidação usada: {dedup_dir.name} | {manticore}", file=sys.stderr)
    if divergentes:
        print(f"AVISO: {divergentes} pedidos com nº de reivindicações diferente do relatório "
              f"(pedido em mais de um lote?)", file=sys.stderr)
    return {rpi: len(v) for rpi, v in por_rpi.items()}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("-o", "--saida", type=Path, required=True, help="pasta de saída do pipeline")
    ap.add_argument("--dedup", type=Path, help="pasta dedup/<...> a usar (padrão: a mais recente)")
    ap.add_argument("--tac", type=Path, help="pasta tac/<...> com o patentes_v4_tai.csv (padrão: a de mesmo nome da dedup)")
    args = ap.parse_args(argv)
    try:
        exportar(args.saida, args.dedup, args.tac)
    except (FileNotFoundError, OSError) as e:
        print(f"ERRO: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
