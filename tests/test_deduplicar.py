"""Testes da deduplicação entre extrações."""
import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import claims_extractor as ce
import deduplicar_concessao as dd


def _lote(pasta: Path, pedido: str, status: str, reivs):
    pasta.mkdir(parents=True)
    with open(pasta / ce.ARQ_REIV, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["pedido", "reivindicacao"])
        w.writerows((pedido, t) for t in reivs)
    with open(pasta / ce.ARQ_REL, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=ce.CAMPOS_REL)
        w.writeheader()
        w.writerow({"arquivo": f"A2/{pedido.replace('-', '_')}.pdf", "pedido": pedido, "status": status,
                    "n_reivindicacoes": len(reivs), "paginas": "2-3", "n_secoes": 1, "n_xml": "",
                    "f1_xml": "", "obs": "", "bytes_csv": 0})


def _ler(caminho):
    with open(caminho, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def test_mesmo_pedido_em_duas_extracoes_nao_duplica_reivindicacoes(tmp_path):
    """O mesmo documento em pastas de ano diferentes (mesma RPI) entra uma vez só."""
    pedido = "BR1120210000011-A2"
    _lote(tmp_path / "2020_20200714", pedido, "ok", ["1. a", "2. b"])
    _lote(tmp_path / "2021_20200714", pedido, "ok", ["1. a", "2. b"])
    dd.deduplicar(tmp_path / "final.csv", [tmp_path / "2020_20200714" / ce.ARQ_REIV,
                                           tmp_path / "2021_20200714" / ce.ARQ_REIV])
    assert [r["reivindicacao"] for r in _ler(tmp_path / "final.csv")] == ["1. a", "2. b"]
    (linha,) = _ler(tmp_path / "final_dedup.csv")
    assert linha["n_reivindicacoes"] == "2" and linha["origem"].startswith("2021_20200714")


def test_copia_com_falha_nao_apaga_a_extracao_boa(tmp_path):
    """Se a cópia na outra pasta falhou (sem_secao), vale a extração que deu certo."""
    pedido = "BR1120210000011-A2"
    _lote(tmp_path / "2020_20200714", pedido, "ok", ["1. a", "2. b"])
    _lote(tmp_path / "2021_20200714", pedido, "sem_secao", [])
    dd.deduplicar(tmp_path / "final.csv", [tmp_path / "2020_20200714" / ce.ARQ_REIV,
                                           tmp_path / "2021_20200714" / ce.ARQ_REIV])
    assert [r["reivindicacao"] for r in _ler(tmp_path / "final.csv")] == ["1. a", "2. b"]
