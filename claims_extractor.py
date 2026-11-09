#!/usr/bin/env python3
"""
claims_extractor — extrai as reivindicações de PDFs de patentes do INPI com
texto nativo, usando apenas PyMuPDF (sem OCR).

Saída: CSV com duas colunas
    pedido        -> BR1120180673331-A2 (padrão) ou 112018067333 (--formato-id numero)
    reivindicacao -> texto completo da reivindicação, com a numeração ("1. Uma memória ...")

Uso:
    python claims_extractor.py <pasta_ou_pdf> [-o reivindicacoes.csv]
           [--formato-id completo|numero] [--por-documento]
           [--relatorio relatorio.csv] [--validar-xml] [--excel]

Pulados (<saida>_pulados.csv): só os PDFs que não geraram reivindicações, com o motivo.

Relatório (<saida>_relatorio.csv), um registro por PDF, com status:
    ok             reivindicações extraídas com numeração contínua
    ok_lacunas     extraídas, mas a numeração tem saltos (ver coluna obs)
    sem_texto      PDF digitalizado (sem camada de texto) — exigiria OCR
    sem_secao      não foi encontrado o quadro reivindicatório
    sem_numeracao  seção encontrada, mas sem reivindicações numeradas
    erro_abertura  PDF corrompido/ilegível
    pdf_vazio      PDF sem páginas
    erro           falha inesperada (mensagem na coluna obs)

Padrões de título, rodapé e linha (21)/(11) adaptados de
IBICT-INPI/pi-patent-pdf-to-st36 (src/patenteia/pdf_text.py e pdf_kind.py).
"""
from __future__ import annotations

import argparse
import csv
import re
import sys
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

import pymupdf

# ------------------------------------------------------------------ padrões
def _espacado(palavra: str) -> str:
    return r"\s*".join(palavra)

# "REIVINDICAÇÕES", "Reivindicações:", "- REIVINDICAÇÕES -", "REIVINDICAÇÃO",
# "REIVINDICAÇÕES EMENDADAS", "R E I V I N D I C A Ç Õ E S", "REINVINDICAÇÕES"...
_VARIANTES = "|".join([
    r"R\s*E\s*I\s*N?\s*V\s*I\s*N?\s*D\s*I\s*C\s*A\s*(?:Ç|C|G)\s*(?:[ÕO]\s*E\s*S|[ÃA]\s*O)",
    _espacado("RENINDICACOES"), _espacado("RENINDICACAO"),
])
RE_TITULO_REIV = re.compile(
    rf"^[\s\-–—•*_]*(?:{_VARIANTES})"
    r"(?:\s+(?:EMENDADAS?|MODIFICADAS?|ALTERADAS?|CORRIGIDAS?))?[\s\-–—:._]*$",
    re.I,
)
RE_OUTRA_SECAO = re.compile(
    r"^[\s\-–—•*_]*(RESUMO|ABSTRACT|RELAT[ÓO]RIO\s+DESCRITIVO|DESENHOS?|"
    r"LISTAGEM\s+DE\s+SEQU[ÊE]NCIAS?)[\s\-–—:._]*$",
    re.I,
)
RE_CONTADOR = re.compile(r"^\s*(\d{1,3})\s*/\s*(\d{1,3})\s*$")            # "3/9", "2 /5"
RE_CONTADOR_PREFIXO = re.compile(r"^\d{1,3}\s*/\s*\d{1,3}\s+(?=\S)")      # "3/9 texto..."
RE_PETICAO_LINHA = re.compile(
    r"^\s*(?:Peti[çc][ãa]o\s+\d+.*|.*\bp[áa]g\.\s*\d{1,3}\s*/\s*\d{1,3})\s*$", re.I)
RE_PETICAO_FIM = re.compile(r"\s+Peti[çc][ãa]o\s+\d+.*?\bp[áa]g\.\s*\d+\s*/\s*\d+\s*$", re.I)
RE_SO_DIGITOS = re.compile(r"^\s*\d{1,4}\s*$")                            # numeração de margem
RE_OCR_DIGITO = re.compile(r"^(\d)\s?[Oo](?=\s|[.\-–—)]|$)")
RE_MARGEM = re.compile(r"^(?:[1-9]?[05])\s+(?=\S)")                # 5, 10, ... 95

RE_INICIO_REIV = re.compile(r"^\s*(\d{1,3})\s*(?:[.\-–—)]|[ºª]\s*[.\-–—)]?)\s*(.*)$")
# número sem pontuação seguido de maiúscula/aspas: "21 COMPOSTO, caracterizado..."
RE_INICIO_SEM_PONTO = re.compile(r"^\s*(\d{1,3})\s+([A-ZÁÉÍÓÚÂÊÔÃÕÇ“\"«].*)$")
MAX_LACUNA = 3        # tolera saltos na numeração (ex.: 1 -> 3) até este tamanho
LINHAS_TOPO = 6       # título/contador precisam estar entre as N primeiras linhas

# (21) BR 112018067333-1 A2 | (11) PI 0617329-4 B1 | (21) BR 202018005367-7 U2
RE_NUM_CAPA = re.compile(
    r"\((?:11|21)\)\s*(BR|PI|MU|DI|C\s*1)?\s*(\d[\d\s.]{5,16}\d)\s*[-–—]\s*(\d)\s+"
    r"([A-Z]\s*[0-9Il])\b", re.I)
RE_NUM_ARQUIVO = re.compile(r"^(?:BR)?(PI|MU|C\d)?(\d{7,14})", re.I)   # BR..., PI..., BRPI...

# hifenização: segmentos que mantêm o hífen ao quebrar linha
CLITICOS = {"se", "o", "a", "os", "as", "lo", "la", "los", "las", "no", "na", "nos", "nas",
            "me", "te", "lhe", "lhes", "vos"}
# prefixos que sempre levam hífen; os demais (anti, micro, semi...) só antes de
# "h" ou da mesma vogal em que terminam (Acordo Ortográfico: anti-inflamatório)
PREFIXOS_SEMPRE = {"pré", "pós", "pró", "ex", "vice", "recém", "além", "aquém", "sem", "bem", "não"}
PREFIXOS_REGRA = {"anti", "contra", "extra", "infra", "intra", "micro", "macro", "mini", "multi",
                  "semi", "sub", "super", "ultra", "auto", "meta", "neo", "pseudo", "co", "bi",
                  "tri", "poli", "mono", "inter", "hiper", "supra", "sobre", "nano"}


def _prefixo_pede_hifen(a: str, b: str) -> bool:
    a, b = a.lower(), b.lower()
    if a in PREFIXOS_SEMPRE:
        return True
    if a in PREFIXOS_REGRA:
        return b.startswith("h") or b[:1] == a[-1:] or (a in {"sub", "sob"} and b[:1] in "br")
    return False


# ------------------------------------------------------------------ texto da página
def linhas_pagina(pagina: pymupdf.Page) -> tuple[list[str], tuple[int, int] | None]:
    """Linhas úteis da página (sem contador, rodapé de petição e numeração
    de margem) e o contador k/N da página, se houver."""
    brutas = [ln.strip() for ln in pagina.get_text("text").splitlines()]
    brutas = [ln for ln in brutas if ln]
    contador = None
    # o contador pode vir depois de ruído de carimbo/rubrica ("trU •.", "_")
    for k, ln in enumerate(brutas[:LINHAS_TOPO]):
        m = RE_CONTADOR.match(ln)
        if m and int(m.group(1)) <= int(m.group(2)):
            contador = (int(m.group(1)), int(m.group(2)))
            # descarta o ruído curto acima do contador
            if all(len(x) <= 12 for x in brutas[:k]):
                brutas = brutas[k + 1:]
            else:
                brutas = brutas[:k] + brutas[k + 1:]
            break
    out = []
    for ln in brutas:
        # camada de texto gerada por OCR na origem: "1 O." -> "10.", "2O" -> "20"
        ln = RE_OCR_DIGITO.sub(lambda m: m.group(1) + "0", ln)
        if RE_PETICAO_LINHA.match(ln) or RE_SO_DIGITOS.match(ln):
            continue
        ln = RE_PETICAO_FIM.sub("", RE_CONTADOR_PREFIXO.sub("", ln)).strip()
        if ln:
            out.append(ln)
    return out, contador


def tem_texto(doc: pymupdf.Document) -> bool:
    """Falso para PDFs digitalizados: só a folha de rosto tem camada de texto."""
    pags = [doc[i] for i in range(1, doc.page_count)] or [doc[0]]
    com_texto = sum(1 for p in pags if len(p.get_text("text").strip()) >= 30)
    return com_texto / len(pags) >= 0.3


class Hifenizacao:
    """Decide se 'pré-\\ncarga' vira 'pré-carga' ou 'especifi-\\ncação' vira
    'especificação', usando o vocabulário do próprio documento."""

    def __init__(self, texto_documento: str):
        self.vocab = Counter(w.lower() for w in re.findall(r"[\wÀ-ÿ]+(?:-[\wÀ-ÿ]+)*", texto_documento))

    def juntar(self, antes: str, depois: str) -> str:
        m1 = re.search(r"([\wÀ-ÿ]+)-$", antes)
        m2 = re.match(r"([\wÀ-ÿ]+)", depois)
        if not m1 or not m2:
            return antes + depois
        a, b = m1.group(1), m2.group(1)
        com, sem = f"{a}-{b}".lower(), f"{a}{b}".lower()
        if self.vocab[com] > self.vocab[sem]:
            manter = True
        elif self.vocab[sem] > self.vocab[com]:
            manter = False
        else:
            manter = (b.lower() in CLITICOS or _prefixo_pede_hifen(a, b)
                      or b[:1].isupper() or a.isupper() or any(c.isdigit() for c in a + b))
        return antes + depois if manter else antes[:-1] + depois


def juntar_linhas(linhas: list[str], hif: Hifenizacao) -> str:
    texto = ""
    for ln in linhas:
        if not texto:
            texto = ln
        elif texto.endswith("-") and len(texto) > 1 and texto[-2].isalpha():
            texto = hif.juntar(texto, ln)
        else:
            texto += " " + ln
    return re.sub(r"\s+", " ", texto).strip()


# ------------------------------------------------------------------ identificação
def _kind(valor: str) -> str:
    v = re.sub(r"\s+", "", valor).upper()
    return v[0] + {"I": "1", "L": "1", "Z": "2", "O": "0"}.get(v[1], v[1]) if len(v) == 2 else v


def identificar(doc: pymupdf.Document, pdf: Path) -> dict:
    m_arq = RE_NUM_ARQUIVO.match(pdf.stem)
    prefixo_arq = (m_arq.group(1) or "BR").upper() if m_arq else "BR"
    num_arq = m_arq.group(2) if m_arq else None

    texto = doc[0].get_text("text") if doc.page_count else ""
    candidatos = []
    for m in RE_NUM_CAPA.finditer(texto):
        num = re.sub(r"\D", "", m.group(2))
        pref = re.sub(r"\s", "", m.group(1) or prefixo_arq).upper()
        candidatos.append((pref, num, m.group(3), _kind(m.group(4))))
    # só aceita o número da capa quando confere com o nome do arquivo
    # (o nome do arquivo pode trazer o DV colado: BR1020160107504A2.pdf)
    escolhido = next((c for c in candidatos if num_arq and num_arq.startswith(c[1])), None)
    if escolhido is None and candidatos and not num_arq:
        escolhido = candidatos[0]
    if escolhido:
        pref, num, dv, kind = escolhido
        return {"prefixo": pref, "numero": num, "dv": dv, "kind": kind}
    return {"prefixo": prefixo_arq, "numero": num_arq or pdf.stem, "dv": "", "kind": ""}


def formatar_id(ident: dict, formato: str) -> str:
    if formato == "numero":
        return ident["numero"]
    s = f"{ident['prefixo']}{ident['numero']}{ident['dv']}"
    return f"{s}-{ident['kind']}" if ident["kind"] else s


# ------------------------------------------------------------------ seções
@dataclass
class Secao:
    paginas: list[int] = field(default_factory=list)
    linhas: list[str] = field(default_factory=list)
    titulo: str = ""


def e_titulo_reiv(ln: str) -> bool:
    """Título da seção: em maiúsculas ou capitalizado. Evita confundir com a
    palavra "reivindicação" isolada numa linha de texto justificado."""
    letras = [c for c in ln if c.isalpha()]
    return bool(RE_TITULO_REIV.match(ln)) and bool(letras) and letras[0].isupper()


def parece_reivindicacao(linhas: list[str]) -> bool:
    texto = " ".join(linhas[:40]).lower()
    return bool(re.search(r"reivindica[çc]|caracterizad|compreende", texto))


def _inicio_sem_titulo(linhas: list[str], cont) -> bool:
    """Página que abre o quadro sem título: contador 1/N e primeira linha "1."."""
    return bool(cont and cont[0] == 1 and linhas and re.match(r"^\s*1\s*[.\-–—)]", linhas[0])
                and parece_reivindicacao(linhas))


def localizar_secoes(paginas: list[tuple[list[str], tuple[int, int] | None]],
                     sem_titulo: bool = False) -> list[Secao]:
    secoes: list[Secao] = []
    i = 1                                   # página 0 é a folha de rosto
    while i < len(paginas):
        linhas, cont = paginas[i]
        if sem_titulo:
            idx = -1 if _inicio_sem_titulo(linhas, cont) else None
        else:
            idx = next((k for k, ln in enumerate(linhas[:LINHAS_TOPO]) if e_titulo_reiv(ln)), None)
        if idx is None:
            i += 1
            continue
        sec = Secao(paginas=[i], linhas=linhas[idx + 1:], titulo=linhas[idx] if idx >= 0 else "")
        total = cont[1] if cont and cont[0] == 1 else None
        j = i + 1
        while j < len(paginas):
            if total is not None and len(sec.paginas) >= total:
                break
            lj, cj = paginas[j]
            if any(e_titulo_reiv(x) or (RE_OUTRA_SECAO.match(x) and x.isupper()) for x in lj[:LINHAS_TOPO]):
                break
            if cj:
                if total is not None and cj[1] != total:
                    break                   # numeração de outra seção (ex.: desenhos 1/13)
                if total is not None and cj[0] != len(sec.paginas) + 1 and not parece_reivindicacao(lj):
                    break                   # contador fora de ordem e sem cara de reivindicação
                if total is None and cj[0] == 1:
                    break
            elif total is None and not lj:
                break
            sec.paginas.append(j)
            sec.linhas.extend(lj)
            j += 1
        secoes.append(sec)
        i = j
    return secoes


def remover_margem(linhas: list[str]) -> list[str]:
    """Remove numeração de linhas na margem (5, 10, 15...) quando sistemática."""
    conta = lambda n: sum(1 for ln in linhas if re.match(rf"^{n}\s+\S", ln))
    if conta(5) >= 2 and conta(10) >= 2 and conta(15) >= 1:
        return [RE_MARGEM.sub("", ln) for ln in linhas]
    return linhas


def dividir_reivindicacoes(linhas: list[str], hif: Hifenizacao) -> list[tuple[int, str]]:
    """Divide pela numeração crescente. Com número pontuado ("5.", "5 -", "5)")
    aceita saltos de até MAX_LACUNA; sem pontuação, só o número seguinte."""
    linhas = remover_margem(linhas)
    reivs: list[tuple[int, list[str]]] = []
    ultimo = 0
    for ln in linhas:
        n = None
        m = RE_INICIO_REIV.match(ln)
        if m and ultimo < int(m.group(1)) <= ultimo + 1 + (MAX_LACUNA if ultimo else 0):
            n = int(m.group(1))
        else:
            m = RE_INICIO_SEM_PONTO.match(ln)
            if m and int(m.group(1)) == ultimo + 1:
                n = ultimo + 1
        if n is not None:
            reivs.append((n, [ln]))
            ultimo = n
        elif reivs:
            reivs[-1][1].append(ln)
    textos = [(n, juntar_linhas(ls, hif)) for n, ls in reivs]
    return recuperar_lacunas(textos)


def recuperar_lacunas(reivs: list[tuple[int, str]]) -> list[tuple[int, str]]:
    """Se falta o número g entre duas reivindicações, procura "g. Texto" no meio
    da anterior (número que não ficou no início de linha) e separa."""
    out: list[tuple[int, str]] = []
    for idx, (n, t) in enumerate(reivs):
        prox = reivs[idx + 1][0] if idx + 1 < len(reivs) else n + 1
        partes = [(n, t)]
        for g in range(n + 1, prox):
            num, txt = partes[-1]
            m = re.search(rf"(?<=[.;:\s])\s*\b{g}\s*[.\-–—)]\s+(?=[A-ZÁÉÍÓÚÂÊÔÃÕÇ“\"«])", txt)
            if m and m.start() > 20:
                partes[-1] = (num, txt[:m.start()].strip())
                partes.append((g, txt[m.start():].strip()))
        out.extend(partes)
    return out


def escolher_secao(secoes: list[Secao], hif: Hifenizacao) -> tuple[Secao | None, list[tuple[int, str]]]:
    """Com mais de um quadro reivindicatório, fica com o primeiro que tenha
    reivindicações numeradas (o publicado; os seguintes costumam ser emendas
    ou versões anexadas). Ver coluna n_secoes do relatório."""
    for s in secoes:
        r = dividir_reivindicacoes(s.linhas, hif)
        if r:
            return s, r
    return (secoes[0] if secoes else None), []


# ------------------------------------------------------------------ validação (opcional)
def reivs_xml(xml: Path) -> list[str]:
    import xml.etree.ElementTree as ET
    try:
        raiz = ET.parse(xml).getroot()
    except ET.ParseError:
        return []
    return [" ".join("".join(c.itertext()).split()) for c in raiz.findall(".//claims/claim")]


def f1_palavras(extraidas: list[tuple[int, str]], gabarito: list[str]) -> float | None:
    """F1 de palavras entre o texto extraído e o do XML (insensível à segmentação)."""
    if not gabarito or not extraidas:
        return None
    tok = lambda t: re.findall(r"\w+", unicodedata.normalize("NFKC", t).lower())
    a = Counter(w for _, t in extraidas for w in tok(re.sub(r"^\s*\d{1,3}\s*\W*", "", t)))
    b = Counter(w for t in gabarito for w in tok(t))
    comum = sum((a & b).values())
    if not comum:
        return 0.0
    p, r = comum / sum(a.values()), comum / sum(b.values())
    return 2 * p * r / (p + r)


# ------------------------------------------------------------------ principal
def processar(pdf: Path, formato_id: str, validar: bool) -> tuple[list[tuple[str, str]], dict]:
    """Nunca levanta exceção: falhas viram status no relatório."""
    try:
        return _processar(pdf, formato_id, validar)
    except Exception as e:  # noqa: BLE001
        m = RE_NUM_ARQUIVO.match(pdf.stem)
        return [], {"arquivo": str(pdf), "pedido": m.group(0) if m else pdf.stem, "status": "erro",
                    "n_reivindicacoes": 0, "paginas": "", "n_secoes": 0, "n_xml": "", "f1_xml": "",
                    "obs": f"{type(e).__name__}: {e}"[:200]}


def _processar(pdf: Path, formato_id: str, validar: bool) -> tuple[list[tuple[str, str]], dict]:
    rel = {"arquivo": str(pdf), "pedido": "", "status": "", "n_reivindicacoes": 0,
           "paginas": "", "n_secoes": 0, "n_xml": "", "f1_xml": "", "obs": ""}
    obs = []
    try:
        doc = pymupdf.open(pdf)
    except Exception as e:  # noqa: BLE001
        rel.update(status="erro_abertura", obs=str(e))
        return [], rel
    with doc:
        if doc.page_count == 0:
            rel.update(status="pdf_vazio", pedido=pdf.stem)
            return [], rel
        ident = identificar(doc, pdf)
        rel["pedido"] = formatar_id(ident, formato_id)
        if not ident["kind"]:
            obs.append("número/kind não lido na capa; usado o nome do arquivo")
        if not tem_texto(doc):
            rel.update(status="sem_texto", obs="; ".join(obs))
            return [], rel
        paginas = [linhas_pagina(p) for p in doc]
    hif = Hifenizacao("\n".join(" ".join(l) for l, _ in paginas))
    secoes = localizar_secoes(paginas)
    if not secoes:
        secoes = localizar_secoes(paginas, sem_titulo=True)
        if secoes:
            obs.append("quadro sem título (início inferido pela numeração)")
    rel["n_secoes"] = len(secoes)
    sec, reivs = escolher_secao(secoes, hif)
    if sec is None:
        rel.update(status="sem_secao", obs="; ".join(obs))
        return [], rel
    rel["paginas"] = f"{sec.paginas[0] + 1}-{sec.paginas[-1] + 1}"
    if not reivs:
        unica = juntar_linhas(remover_margem(sec.linhas), hif)
        if len(unica) < 40:
            rel.update(status="sem_numeracao", obs="; ".join(obs))
            return [], rel
        reivs = [(1, unica)]               # reivindicação única, sem número no PDF
        obs.append("reivindicação única sem numeração no PDF")
    rel["status"] = "ok"
    rel["n_reivindicacoes"] = len(reivs)
    nums = [n for n, _ in reivs]
    faltam = sorted(set(range(1, nums[-1] + 1)) - set(nums))
    if faltam:
        rel["status"] = "ok_lacunas"
        obs.append("numeração sem: " + ",".join(map(str, faltam)))
    rel["obs"] = "; ".join(obs)
    if validar:
        xmls = sorted(pdf.parent.glob("*.xml"))
        if xmls:
            gab = reivs_xml(xmls[0])
            rel["n_xml"] = len(gab)
            f1 = f1_palavras(reivs, gab)
            rel["f1_xml"] = f"{f1:.4f}" if f1 is not None else ""
    return [(rel["pedido"], texto) for _, texto in reivs], rel


CAMPOS_PULADOS = ["numero", "pedido", "motivo", "detalhe", "arquivo"]


def linha_pulado(rel: dict) -> dict:
    """Registro de um PDF que não gerou reivindicações (status diferente de ok*)."""
    m = RE_NUM_ARQUIVO.match(Path(rel["arquivo"]).stem)
    return {"numero": m.group(2) if m else Path(rel["arquivo"]).stem, "pedido": rel["pedido"], "motivo": rel["status"],
            "detalhe": rel.get("obs", ""), "arquivo": rel["arquivo"]}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("entrada", type=Path, help="PDF ou pasta (busca recursiva por *.pdf)")
    ap.add_argument("-o", "--saida", type=Path, default=Path("reivindicacoes.csv"))
    ap.add_argument("--formato-id", choices=["completo", "numero"], default="completo",
                    help="completo: BR1120180673331-A2 | numero: 112018067333")
    ap.add_argument("--por-documento", action="store_true",
                    help="uma linha por pedido, com as reivindicações separadas por quebra de linha")
    ap.add_argument("--relatorio", type=Path, default=None,
                    help="CSV de diagnóstico por PDF (padrão: <saida>_relatorio.csv)")
    ap.add_argument("--validar-xml", action="store_true",
                    help="compara com o XML ST.36 que estiver na mesma pasta do PDF")
    ap.add_argument("--excel", action="store_true", help="grava em UTF-8 com BOM (abre direto no Excel)")
    args = ap.parse_args(argv)

    pdfs = [args.entrada] if args.entrada.is_file() else sorted(
        p for p in args.entrada.rglob("*.pdf") if not p.name.startswith("._"))  # ignora AppleDouble do macOS
    if not pdfs:
        print("Nenhum PDF encontrado.", file=sys.stderr)
        return 1
    enc = "utf-8-sig" if args.excel else "utf-8"
    relatorio = args.relatorio or args.saida.with_name(args.saida.stem + "_relatorio.csv")
    pulados = args.saida.with_name(args.saida.stem + "_pulados.csv")

    linhas_saida, rels = [], []
    for n, pdf in enumerate(pdfs, 1):
        linhas, rel = processar(pdf, args.formato_id, args.validar_xml)
        rels.append(rel)
        if args.por_documento and linhas:
            linhas = [(linhas[0][0], "\n".join(t for _, t in linhas))]
        linhas_saida.extend(linhas)
        print(f"[{n}/{len(pdfs)}] {rel['pedido']:<22} {rel['status']:<14} {rel['n_reivindicacoes']:>3} reiv.",
              file=sys.stderr)

    with open(args.saida, "w", newline="", encoding=enc) as f:
        w = csv.writer(f)
        w.writerow(["pedido", "reivindicacao"])
        w.writerows(linhas_saida)
    with open(relatorio, "w", newline="", encoding=enc) as f:
        w = csv.DictWriter(f, fieldnames=list(rels[0].keys()))
        w.writeheader()
        w.writerows(rels)

    with open(pulados, "w", newline="", encoding=enc) as f:
        w = csv.DictWriter(f, fieldnames=CAMPOS_PULADOS)
        w.writeheader()
        w.writerows(linha_pulado(r) for r in rels if not r["status"].startswith("ok"))

    c = Counter(r["status"] for r in rels)
    print(f"\n{len(pdfs)} PDFs | {len(linhas_saida)} linhas em {args.saida} | " +
          ", ".join(f"{k}: {v}" for k, v in c.most_common()), file=sys.stderr)
    print(f"Relatório: {relatorio} | Pulados na extração: {pulados}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
