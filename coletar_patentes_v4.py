#!/usr/bin/env python3
"""
Coleta título, resumo e IPCs no Manticore (tabela patentes_v4) para os pedidos de
pedidos_final.csv, casando numero_base com application_number_norm.

Uso (no Terminal do Mac, dentro desta pasta):
    python3 coletar_patentes_v4.py --host <servidor>     # padrão: $MANTICORE_HOST ou localhost

Saídas:
    patentes_v4_tai.csv          pedido, numero_base, application_number, title, abstract,
                                 ipc_txt, ipc_json, first_subclass, nature, status, n_registros
    patentes_v4_nao_encontrados.csv
Só usa a biblioteca padrão do Python; só faz SELECT.
"""
import argparse, csv, json, os, sys, urllib.request
from collections import defaultdict

CAMPOS = ["application_number_norm", "application_number", "title", "abstract",
          "ipc_txt", "classification", "first_subclass", "nature", "status", "country_code"]

def sql(host, porta, consulta):
    req = urllib.request.Request(f"http://{host}:{porta}/sql?mode=raw",
                                 data=consulta.encode("utf-8"), method="POST")
    with urllib.request.urlopen(req, timeout=120) as r:
        resp = json.loads(r.read().decode("utf-8"))
    bloco = resp[0] if isinstance(resp, list) else resp
    if bloco.get("error"):
        raise RuntimeError(bloco["error"])
    return bloco.get("data", [])

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default=os.environ.get("MANTICORE_HOST", "localhost"))
    ap.add_argument("--porta", type=int, default=9308)
    ap.add_argument("--tabela", default="patentes_v4")
    ap.add_argument("--entrada", default="pedidos_final.csv")
    ap.add_argument("--lote", type=int, default=200)
    a = ap.parse_args()

    pedidos = list(csv.DictReader(open(a.entrada, encoding="utf-8")))
    bases = sorted({p["numero_base"] for p in pedidos if p["numero_base"].isdigit()})
    achados = defaultdict(list)
    for i in range(0, len(bases), a.lote):
        lote = bases[i:i + a.lote]
        lista = ",".join(f"'{b}'" for b in lote)          # só dígitos: sem risco de injeção
        q = (f"SELECT {', '.join(CAMPOS)} FROM {a.tabela} "
             f"WHERE application_number_norm IN ({lista}) "
             f"LIMIT {a.lote * 5} OPTION max_matches={a.lote * 5}")
        for r in sql(a.host, a.porta, q):
            achados[r["application_number_norm"]].append(r)
        print(f"  {min(i + a.lote, len(bases))}/{len(bases)} números consultados", file=sys.stderr)

    with open("patentes_v4_tai.csv", "w", newline="", encoding="utf-8") as f, \
         open("patentes_v4_nao_encontrados.csv", "w", newline="", encoding="utf-8") as fn:
        w = csv.writer(f); wn = csv.writer(fn)
        w.writerow(["pedido", "numero_base", "application_number", "title", "abstract", "ipc_txt",
                    "ipc_json", "first_subclass", "nature", "status", "n_registros"])
        wn.writerow(["pedido", "numero_base", "prefixo", "kind"])
        enc = 0
        for p in pedidos:
            regs = achados.get(p["numero_base"], [])
            # PI/MU antigos têm 7 dígitos: prefere o registro do mesmo tipo (PI x MU)
            if len(regs) > 1 and p["prefixo"] in ("PI", "MU"):
                regs = [r for r in regs if r["application_number"].upper().startswith(p["prefixo"])] or regs
            if not regs:
                wn.writerow([p["pedido"], p["numero_base"], p["prefixo"], p["kind"]]); continue
            r = regs[0]; enc += 1
            ipcs = ""
            try:
                ipcs = json.dumps([x["text"] for x in json.loads(r["classification"]).get("ipc", [])],
                                  ensure_ascii=False)
            except Exception:
                pass
            w.writerow([p["pedido"], p["numero_base"], r["application_number"], r["title"], r["abstract"],
                        r["ipc_txt"], ipcs, r["first_subclass"], r["nature"], r["status"], len(regs)])
    print(f"\nencontrados: {enc}/{len(pedidos)} | não encontrados: {len(pedidos) - enc}", file=sys.stderr)
    print("arquivos: patentes_v4_tai.csv, patentes_v4_nao_encontrados.csv", file=sys.stderr)

if __name__ == "__main__":
    main()
