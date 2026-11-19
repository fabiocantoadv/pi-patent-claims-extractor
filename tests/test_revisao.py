"""Testes do CSV de revisão por RPI (extrações sintéticas, sem PDFs)."""
import csv
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import claims_extractor as ce
import deduplicar_concessao as dd
import exportar_revisao as er


def _escreve(lote: Path, docs):
    """docs: (arquivo, pedido, status, [reivindicações], obs). Grava os CSVs de um lote."""
    lote.mkdir(parents=True)
    with open(lote / ce.ARQ_REIV, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["pedido", "reivindicacao"])
        for _, pedido, status, reivs, _ in docs:
            w.writerows((pedido, t) for t in reivs)
    with open(lote / ce.ARQ_REL, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=ce.CAMPOS_REL)
        w.writeheader()
        for arquivo, pedido, status, reivs, obs in docs:
            w.writerow({"arquivo": arquivo, "pedido": pedido, "status": status, "n_reivindicacoes": len(reivs),
                        "paginas": "2-3" if reivs else "", "n_secoes": 1, "n_xml": "", "f1_xml": "",
                        "obs": obs, "bytes_csv": 0})


@pytest.fixture
def saida(tmp_path):
    """Duas RPIs (20211109, 20211116); a primeira tem um lote em outra pasta de ano."""
    s = tmp_path / "saidas"
    _escreve(s / "extracoes" / "2021_20211109", [
        ("A2/BR1120210000011_A2.pdf", "BR1120210000011-A2", "ok", ["1. a1", "2. a2"], ""),       # perde para o B1
        ("A2/BR1120210000044_A2.pdf", "BR1120210000044-A2", "ok", ["1. d1"], ""),               # B1 do mesmo pedido falhou
        ("A2/BR1120210000044_B1.pdf", "BR1120210000044-B1", "sem_secao", [], "quadro não achado"),
        ("A2/BR1120210000033_A2.pdf", "BR1120210000033-A2", "sem_secao", [], "quadro não achado"),  # só falhou: fica fora
    ])
    _escreve(s / "extracoes" / "2026_20211109", [
        ("A2/BR1120210000022_A2.pdf", "BR1120210000022-A2", "ok_lacunas", ["1. c1", "3. c3"], "numeração sem: 2"),
    ])
    _escreve(s / "extracoes" / "2021_20211116", [
        ("B1/BR1120210000011_B1.pdf", "BR1120210000011-B1", "ok", ["1. b1", "2. b2", "3. b3"], ""),
    ])
    dd_dir = s / "dedup" / "2026-11-19_consolidado"
    dd_dir.mkdir(parents=True)
    dd.deduplicar(dd_dir / "final.csv", sorted((s / "extracoes").glob(f"*/{ce.ARQ_REIV}")))
    return s


def ler(caminho):
    with open(caminho, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def test_rpi_do_lote():
    assert er.rpi_do_lote("2021_20211109") == "20211109"
    assert er.rpi_do_lote("2026_20211109") == "20211109"
    assert er.rpi_do_lote("20180508") == "20180508"
    assert er.rpi_do_lote("2021_20211109_0002") == "20211109"
    assert er.rpi_do_lote("sem-data") == "sem-data"


def test_um_csv_por_rpi_coerente_com_a_deduplicacao(saida):
    er.exportar(saida)
    assert sorted(p.name for p in (saida / "revisao").glob("*.csv")) == ["20211109.csv", "20211116.csv"]
    r1109 = {r["pedido"]: r for r in ler(saida / "revisao" / "20211109.csv")}
    r1116 = {r["pedido"]: r for r in ler(saida / "revisao" / "20211116.csv")}
    # lotes de pastas de ano diferentes, mesma RPI, no mesmo arquivo
    assert set(r1109) == {"BR1120210000022-A2", "BR1120210000044-A2"}
    assert {r["lote"] for r in r1109.values()} == {"2021_20211109", "2026_20211109"}
    # o A2 perdeu para a concessão: só aparece uma vez, na RPI do B1
    assert set(r1116) == {"BR1120210000011-B1"}
    # quem só falhou não entra
    assert "BR1120210000033-A2" not in r1109


def test_colunas_e_valores(saida):
    er.exportar(saida)
    assert list(ler(saida / "revisao" / "20211116.csv")[0]) == er.COLUNAS
    b1 = ler(saida / "revisao" / "20211116.csv")[0]
    assert (b1["rpi"], b1["lote"], b1["numero_base"], b1["kind"]) == ("20211116", "2021_20211116", "112021000001", "B1")
    assert (b1["prefixo"], b1["dv"]) == ("BR", "1")
    assert b1["reivindicacoes"] == "1. b1\n2. b2\n3. b3" and b1["n_reivindicacoes"] == "3"
    assert (b1["arquivo"], b1["status_extracao"], b1["paginas"]) == ("B1/BR1120210000011_B1.pdf", "ok", "2-3")
    lac = {r["pedido"]: r for r in ler(saida / "revisao" / "20211109.csv")}["BR1120210000022-A2"]
    assert lac["status_extracao"] == "ok_lacunas" and lac["obs"] == "numeração sem: 2"


def test_avisa_quando_outra_versao_do_pedido_falhou(saida):
    er.exportar(saida)
    d = {r["pedido"]: r for r in ler(saida / "revisao" / "20211109.csv")}["BR1120210000044-A2"]
    assert "BR1120210000044-B1 (sem_secao)" in d["obs"]


def test_manticore_opcional(saida):
    er.exportar(saida)
    assert ler(saida / "revisao" / "20211116.csv")[0]["title"] == ""             # sem tac: colunas vazias
    tac = saida / "tac" / "2026-11-19_consolidado"
    tac.mkdir(parents=True)
    with open(tac / "patentes_v4_tai.csv", "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["pedido", "numero_base", "application_number", "title", "abstract", "ipc_txt",
                    "ipc_json", "first_subclass", "nature", "status", "n_registros"])
        w.writerow(["BR1120210000011-B1", "112021000001", "BR 11 2021 000001-1", "Um título", "Um resumo",
                    "A01B 1/00", '["A01B 1/00"]', "A01B", "PI", "granted", 1])
    er.exportar(saida)
    b1 = ler(saida / "revisao" / "20211116.csv")[0]
    assert (b1["title"], b1["abstract"], b1["nature"], b1["status_patente"], b1["n_registros"]) == \
           ("Um título", "Um resumo", "PI", "granted", "1")
    assert ler(saida / "revisao" / "20211109.csv")[0]["title"] == ""             # sem match: vazio


def test_sem_consolidacao_da_erro_claro(tmp_path, capsys):
    assert er.main(["-o", str(tmp_path)]) == 1
    assert "consolidação" in capsys.readouterr().err
