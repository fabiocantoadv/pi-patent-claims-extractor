"""Testes dos modos do orquestrador (--only extract / consolidate), sem PDFs reais."""
import csv
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import claims_extractor as ce
import pipeline


@pytest.fixture(autouse=True)
def processar_falso(monkeypatch):
    """Cada PDF vira 2 reivindicações; o pedido sai do nome do arquivo (BR..._A2.pdf)."""
    def falso(pdf, formato_id, validar):
        pedido = pdf.stem.replace("_", "-")
        rel = {"arquivo": str(pdf), "pedido": pedido, "status": "ok", "n_reivindicacoes": 2,
               "paginas": "2", "n_secoes": 1, "n_xml": "", "f1_xml": "", "obs": ""}
        return [(pedido, f"1. a {pedido}"), (pedido, f"2. b {pedido}")], rel
    monkeypatch.setattr(ce, "processar", falso)


def cria_lote(base: Path, nome: str, *arquivos: str) -> Path:
    pasta = base / nome
    pasta.mkdir(parents=True)
    for a in arquivos:
        (pasta / a).write_bytes(b"")
    return pasta


def linhas(caminho: Path):
    with open(caminho, encoding="utf-8", newline="") as f:
        return list(csv.reader(f))[1:]


def test_only_extract_nao_deduplica(tmp_path):
    entrada = tmp_path / "entrada"
    cria_lote(entrada, "20180508", "BR1000000000001_A2.pdf")
    saida = tmp_path / "saida"
    assert pipeline.main(["-i", str(entrada), "-o", str(saida), "--only", "extract"]) == 0
    assert (saida / "extracoes" / "20180508" / ce.ARQ_REIV).exists()
    assert not (saida / "dedup").exists()


def test_consolidate_cruza_lotes_ja_extraidos_sem_pdfs(tmp_path):
    """Fluxo baixar -> extrair -> apagar PDFs -> consolidar: o A2 de um lote perde para o B1 de outro."""
    saida = tmp_path / "saida"
    for lote, arquivo in (("20180508", "BR1000000000001_A2.pdf"), ("20261020", "BR1000000000001_B1.pdf")):
        entrada = tmp_path / "tmp_entrada"
        cria_lote(entrada, lote, arquivo)
        assert pipeline.main(["-i", str(entrada), "-o", str(saida), "--only", "extract"]) == 0
        for pdf in entrada.rglob("*.pdf"):          # apaga o lote depois de extrair
            pdf.unlink()
        (entrada / lote).rmdir()
        entrada.rmdir()
    assert pipeline.main(["-o", str(saida), "--only", "consolidate"]) == 0
    final = next((saida / "dedup").glob("*_consolidado/final.csv"))
    assert {r[0] for r in linhas(final)} == {"BR1000000000001-B1"}       # só a concessão
    assert len(linhas(final)) == 2


def test_consolidate_sem_extracoes_falha(tmp_path):
    assert pipeline.main(["-o", str(tmp_path / "vazia"), "--only", "consolidate"]) == 1


def test_consolidate_ignora_lote_sem_relatorio(tmp_path, capsys):
    saida = tmp_path / "saida"
    entrada = tmp_path / "e"
    cria_lote(entrada, "L1", "BR1000000000001_A2.pdf")
    cria_lote(entrada, "L2", "BR1000000000002_A2.pdf")
    pipeline.main(["-i", str(entrada), "-o", str(saida), "--only", "extract"])
    (saida / "extracoes" / "L2" / ce.ARQ_REL).unlink()
    assert pipeline.main(["-o", str(saida), "--only", "consolidate"]) == 0
    assert "L2 ignorado" in capsys.readouterr().err
    final = next((saida / "dedup").glob("*_consolidado/final.csv"))
    assert {r[0] for r in linhas(final)} == {"BR1000000000001-A2"}


def test_execucao_completa_continua_igual(tmp_path):
    entrada = tmp_path / "entrada"
    cria_lote(entrada, "L1", "BR1000000000001_A2.pdf")
    saida = tmp_path / "saida"
    assert pipeline.main(["-i", str(entrada), "-o", str(saida)]) == 0
    assert next((saida / "dedup").glob("*_entrada/final.csv")).exists()


@pytest.mark.parametrize("argv", [
    ["-o", "x", "--only", "extract"],                                      # falta -i
    ["-o", "x"],                                                           # falta -i
    ["-i", "e", "-o", "x", "--only", "extract", "--host", "h"],            # Manticore com extract
    ["-o", "x", "--only", "consolidate", "--redo"],                        # --redo com consolidate
])
def test_combinacoes_invalidas(argv, tmp_path):
    argv = [str(tmp_path / a) if a in ("x", "e") else a for a in argv]
    (tmp_path / "e").mkdir(exist_ok=True)
    with pytest.raises(SystemExit) as e:
        pipeline.main(argv)
    assert e.value.code == 2
