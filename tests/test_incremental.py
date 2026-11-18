"""Testes da extração incremental/retomável (sem PDFs reais: `processar` é simulado)."""
import csv
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import claims_extractor as ce


@pytest.fixture
def lote(tmp_path, monkeypatch):
    """Lote com 3 PDFs vazios (A, B, C) e um `processar` falso; devolve (entrada, chamadas)."""
    entrada = tmp_path / "lote"
    (entrada / "A2").mkdir(parents=True)
    for nome in ("BR1000000000001_A2.pdf", "BR1000000000002_A2.pdf", "BR1000000000003_A2.pdf"):
        (entrada / "A2" / nome).write_bytes(b"")
    chamadas = []

    def falso(pdf, formato_id, validar):
        chamadas.append(pdf.name)
        pedido = pdf.stem.replace("_", "-")
        rel = {"arquivo": str(pdf), "pedido": pedido, "status": "ok", "n_reivindicacoes": 2,
               "paginas": "2-3", "n_secoes": 1, "n_xml": "", "f1_xml": "", "obs": ""}
        if "0003" in pdf.name:
            rel.update(status="sem_secao", n_reivindicacoes=0)
            return [], rel
        return [(pedido, f"1. texto de {pedido}"), (pedido, f"2. outro de {pedido}")], rel

    monkeypatch.setattr(ce, "processar", falso)
    return entrada, chamadas


def ler(caminho):
    with open(caminho, encoding="utf-8", newline="") as f:
        return list(csv.reader(f))


def test_primeira_execucao_grava_tudo(lote, tmp_path):
    entrada, chamadas = lote
    pasta = tmp_path / "saida"
    c = ce.extrair_incremental(entrada, pasta)
    assert c == {"ok": 2, "sem_secao": 1}
    linhas = ler(pasta / ce.ARQ_REIV)
    assert linhas[0] == ["pedido", "reivindicacao"] and len(linhas) == 1 + 4
    assert len(ler(pasta / ce.ARQ_REL)) == 1 + 3
    assert len(ler(pasta / ce.ARQ_PUL)) == 1 + 1                  # só o sem_secao
    assert ler(pasta / ce.ARQ_REL)[1][0] == "A2/BR1000000000001_A2.pdf"   # caminho relativo ao lote


def test_retomada_reaproveita_e_nao_duplica(lote, tmp_path):
    entrada, chamadas = lote
    pasta = tmp_path / "saida"
    ce.extrair_incremental(entrada, pasta)
    chamadas.clear()
    ce.extrair_incremental(entrada, pasta)
    assert chamadas == []                                          # nada reprocessado
    assert len(ler(pasta / ce.ARQ_REIV)) == 1 + 4                  # sem linhas duplicadas


def test_pdf_novo_e_processado_na_retomada(lote, tmp_path):
    entrada, chamadas = lote
    pasta = tmp_path / "saida"
    ce.extrair_incremental(entrada, pasta)
    (entrada / "A2" / "BR1000000000004_A2.pdf").write_bytes(b"")
    chamadas.clear()
    ce.extrair_incremental(entrada, pasta)
    assert chamadas == ["BR1000000000004_A2.pdf"]
    assert len(ler(pasta / ce.ARQ_REIV)) == 1 + 6


def test_redo_refaz_tudo(lote, tmp_path):
    entrada, chamadas = lote
    pasta = tmp_path / "saida"
    ce.extrair_incremental(entrada, pasta)
    chamadas.clear()
    ce.extrair_incremental(entrada, pasta, redo=True)
    assert sorted(chamadas) == ["BR1000000000001_A2.pdf", "BR1000000000002_A2.pdf", "BR1000000000003_A2.pdf"]
    assert len(ler(pasta / ce.ARQ_REIV)) == 1 + 4


def test_queda_entre_csv_e_relatorio_nao_duplica(lote, tmp_path):
    """Reivindicações gravadas sem a linha do relatório (queda no meio) são descartadas."""
    entrada, chamadas = lote
    pasta = tmp_path / "saida"
    ce.extrair_incremental(entrada, pasta)
    # simula: o último PDF teve o CSV gravado mas não o relatório
    linhas_rel = (pasta / ce.ARQ_REL).read_text(encoding="utf-8").splitlines(keepends=True)
    (pasta / ce.ARQ_REL).write_text("".join(linhas_rel[:-1]), encoding="utf-8")   # tira o C (sem_secao)
    with open(pasta / ce.ARQ_REIV, "a", encoding="utf-8", newline="") as f:
        f.write("BR1000000000003-A2,lixo órfão\r\n")
    chamadas.clear()
    ce.extrair_incremental(entrada, pasta)
    assert chamadas == ["BR1000000000003_A2.pdf"]
    texto = (pasta / ce.ARQ_REIV).read_text(encoding="utf-8")
    assert "lixo" not in texto and len(ler(pasta / ce.ARQ_REIV)) == 1 + 4


def test_linha_truncada_do_relatorio_e_descartada(lote, tmp_path):
    entrada, chamadas = lote
    pasta = tmp_path / "saida"
    ce.extrair_incremental(entrada, pasta)
    with open(pasta / ce.ARQ_REL, "ab") as f:
        f.write(b"A2/BR10000000000")                              # linha cortada no meio
    chamadas.clear()
    ce.extrair_incremental(entrada, pasta)
    assert chamadas == []                                          # os 3 válidos foram reaproveitados
    assert len(ler(pasta / ce.ARQ_REL)) == 1 + 3


def test_saida_anterior_de_formato_desconhecido_recomeca(lote, tmp_path):
    """Pasta com saída sem a coluna bytes_csv (de outro modo de execução): refaz do zero."""
    entrada, chamadas = lote
    pasta = tmp_path / "saida"
    pasta.mkdir()
    (pasta / ce.ARQ_REL).write_text("arquivo,pedido,status\nx.pdf,BR1-A2,ok\n", encoding="utf-8")
    (pasta / ce.ARQ_REIV).write_text("pedido,reivindicacao\nBR1-A2,1. velho\n", encoding="utf-8")
    ce.extrair_incremental(entrada, pasta)
    assert len(chamadas) == 3
    assert "velho" not in (pasta / ce.ARQ_REIV).read_text(encoding="utf-8")


def _cria_pdf(caminho: Path, n: int) -> None:
    """PDF mínimo com capa e um quadro reivindicatório de 2 reivindicações (texto nativo)."""
    import pymupdf
    doc = pymupdf.open()
    capa = doc.new_page()
    capa.insert_text((72, 72), f"(21) BR 11201800000{n}-1 A2\nCapa do documento numero {n} do pedido")
    quadro = doc.new_page()
    quadro.insert_text((72, 72), "1 / 1\nREIVINDICAÇÕES\n"
                       f"1. Dispositivo {n}, caracterizado por compreender uma parte principal e outra parte.\n"
                       "2. Dispositivo conforme a reivindicação 1, caracterizado por ser leve e resistente.")
    doc.save(caminho)
    doc.close()


def test_paralelo_grava_o_mesmo_que_o_sequencial(tmp_path):
    """Sem simulação: os processos de apoio rodam o `processar` de verdade."""
    entrada = tmp_path / "lote"
    (entrada / "A2").mkdir(parents=True)
    for n in range(1, 8):
        _cria_pdf(entrada / "A2" / f"BR11201800000{n}_A2.pdf", n)
    seq, par = tmp_path / "seq", tmp_path / "par"
    ce.extrair_incremental(entrada, seq, workers=1)
    ce.extrair_incremental(entrada, par, workers=3)
    linhas = (seq / ce.ARQ_REIV).read_text(encoding="utf-8")
    assert linhas.count("Dispositivo") >= 7                       # os PDFs sintéticos foram mesmo lidos
    for nome in (ce.ARQ_REIV, ce.ARQ_REL, ce.ARQ_PUL):
        assert (seq / nome).read_bytes() == (par / nome).read_bytes(), nome
