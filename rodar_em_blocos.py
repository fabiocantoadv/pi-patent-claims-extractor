"""Processa a pasta em blocos com limite de tempo; retomável (pula PDFs já feitos)."""
import csv, json, sys, time, os
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import claims_extractor as ce
entrada, limite = Path(sys.argv[1]), float(sys.argv[2])
dirp = Path(__file__).resolve().parent / "_parcial"; dirp.mkdir(exist_ok=True)
lista = dirp / "lista.txt"
if not lista.exists():
    pdfs = sorted(str(p) for p in entrada.rglob("*.pdf") if not p.name.startswith("._"))
    lista.write_text("\n".join(pdfs))
pdfs = lista.read_text().splitlines()
feitos = set()
relf = dirp / "relatorio.csv"
if relf.exists():
    feitos = {r["arquivo"] for r in csv.DictReader(open(relf, encoding="utf-8"))}
pulf = dirp / "pulados.csv"
novo_pul = not pulf.exists()
t0 = time.time(); n = 0
novo_rel = not relf.exists()
with open(dirp / "reivindicacoes.csv", "a", newline="", encoding="utf-8") as fo, \
     open(relf, "a", newline="", encoding="utf-8") as fr, \
     open(pulf, "a", newline="", encoding="utf-8") as fp:
    wo = csv.writer(fo); wr = None
    wp = csv.DictWriter(fp, fieldnames=ce.CAMPOS_PULADOS)
    if novo_pul: wp.writeheader()
    for p in pdfs:
        if p in feitos: continue
        if time.time() - t0 > limite: break
        linhas, rel = ce.processar(Path(p), "completo", False)
        wo.writerows(linhas)
        if wr is None:
            wr = csv.DictWriter(fr, fieldnames=list(rel.keys()))
            if novo_rel: wr.writeheader()
        wr.writerow(rel)
        if not rel["status"].startswith("ok"):
            wp.writerow(ce.linha_pulado(rel))
        fo.flush(); fr.flush(); fp.flush(); n += 1
print(f"bloco: {n} PDFs | total feito: {len(feitos)+n}/{len(pdfs)}")
