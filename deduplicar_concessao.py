"""Junta CSVs do claims_extractor e deduplica por pedido, preferindo a concessão.

Uso: python deduplicar_concessao.py saida.csv extracao1.csv [extracao2.csv ...]
Cada extracao.csv precisa do <nome>_relatorio.csv ao lado (para mapear arquivo -> pedido).

Regra: agrupa pelo número do pedido (sem prefixo, dígito verificador e kind). Se o grupo
tem um documento de concessão (kind B*, Y*, F*, C*), fica só com ele; senão, fica com a
publicação (A*, U*, E*) ou, na falta de kind, com o que tiver mais reivindicações.
"""
import csv, re, sys
from collections import defaultdict
from pathlib import Path

CONCESSAO = ("B", "Y", "F", "C")
PRIORIDADE = lambda kind: 0 if kind[:1] in CONCESSAO else (1 if kind else 2)

def numero_base(arquivo: str) -> str:
    """Número do pedido sem prefixo, dígito verificador e kind.
    Atual: 12 dígitos (ex.: 112018067333); antigo PI/MU: 7 dígitos (ex.: 0617329).
    Nomes de arquivo que já trazem o DV (13 ou 8 dígitos) são cortados."""
    stem = Path(arquivo).stem
    m = re.match(r"^(?:BR)?(PI|MU|C\d)?(\d{7,14})", stem, re.I)
    if not m:
        return stem
    dig = m.group(2)
    if m.group(1) or len(dig) <= 8:
        return dig[:7]
    return dig[:12]

def main():
    if len(sys.argv) < 3:
        sys.exit(__doc__)
    saida, entradas = Path(sys.argv[1]), [Path(p) for p in sys.argv[2:]]
    docs = {}                                        # pedido -> dict(base, kind, n, origem)
    linhas = defaultdict(list)
    for e in entradas:
        rel = e.with_name(e.stem + "_relatorio.csv")
        usados = {}
        for r in csv.DictReader(open(rel, encoding="utf-8")):
            if r["status"].startswith("ok") and r.get("usado_no_csv", "sim") == "sim":
                usados[r["pedido"]] = r
        for pid, txt in csv.reader(open(e, encoding="utf-8")):
            if pid == "pedido": continue
            linhas[pid].append(txt)
        for pid, r in usados.items():
            kind = pid.rsplit("-", 1)[1] if "-" in pid else ""
            docs[pid] = {"base": numero_base(r["arquivo"]), "kind": kind,
                         "n": len(linhas[pid]), "origem": e.name}

    grupos = defaultdict(list)
    for pid, d in docs.items():
        grupos[d["base"]].append(pid)

    escolhidos, descartados = [], []
    for base, pids in grupos.items():
        pids.sort(key=lambda p: (PRIORIDADE(docs[p]["kind"]), -docs[p]["n"]))
        escolhidos.append(pids[0]); descartados += pids[1:]

    with open(saida, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f); w.writerow(["pedido", "reivindicacao"])
        for pid in sorted(escolhidos):
            w.writerows((pid, t) for t in linhas[pid])

    with open(saida.with_name(saida.stem + "_dedup.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f); w.writerow(["numero_base", "pedido", "kind", "n_reivindicacoes", "origem", "mantido"])
        for base, pids in sorted(grupos.items()):
            for p in pids:
                d = docs[p]; w.writerow([base, p, d["kind"], d["n"], d["origem"], "sim" if p == pids[0] else "nao"])

    from collections import Counter
    print(f"documentos de entrada: {len(docs)} | pedidos distintos: {len(grupos)} | descartados: {len(descartados)}")
    print("mantidos por kind:", dict(Counter(docs[p]['kind'] or 'sem' for p in escolhidos)))
    print("descartados por kind:", dict(Counter(docs[p]['kind'] or 'sem' for p in descartados)))
    print("mantidos por origem:", dict(Counter(docs[p]['origem'] for p in escolhidos)))
    print("linhas no CSV final:", sum(len(linhas[p]) for p in escolhidos))


if __name__ == "__main__":
    main()
