"""Testes das heurísticas de texto (sem PDFs). Rodar: python3 -m pytest -q  ou  python3 tests/test_parsing.py"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import claims_extractor as ce
import deduplicar_concessao as dd  # noqa: F401  (garante que importa)

HIF = ce.Hifenizacao("pré-carga especificação considera-se antiaprisionamento")


def test_titulo_reivindicacoes_variantes():
    for t in ["REIVINDICAÇÕES", "Reivindicações:", "- REIVINDICAÇÕES -", "REIVINDICAÇÃO",
              "REIVINDICAÇÕES EMENDADAS", "R E I V I N D I C A Ç Õ E S", "REINVINDICAÇÕES",
              "REIVINDICAGÕES", "REIVINDICACOES"]:
        assert ce.e_titulo_reiv(t), t
    # a palavra isolada em texto justificado não é título
    assert not ce.e_titulo_reiv("reivindicação")


def test_divisao_numeracao_e_lacunas():
    linhas = ["1. Uma memória, compreendendo:", "uma célula;",
              "2. A memória, de acordo com a reivindicação 1,", "em que X.",
              "21 COMPOSTO, caracterizado por", "4. Método de acordo com"]
    r = ce.dividir_reivindicacoes(linhas, HIF)
    assert [n for n, _ in r] == [1, 2, 4]          # "21 COMPOSTO" não é 3
    assert r[0][1] == "1. Uma memória, compreendendo: uma célula;"


def test_numero_sem_ponto_sequencial():
    r = ce.dividir_reivindicacoes(["1. COMPOSTO, caracterizado.", "2 COMPOSTO, de acordo com a reivindicação 1."], HIF)
    assert [n for n, _ in r] == [1, 2]


def test_margem_e_ocr():
    linhas = ["5 texto a", "10 texto b", "5 texto c", "10 10. Processo de acordo", "15 texto d"]
    assert ce.remover_margem(linhas)[3] == "10. Processo de acordo"
    assert ce.RE_OCR_DIGITO.sub(lambda m: m.group(1) + "0", "1 O. MÉTODO") == "10. MÉTODO"


def test_hifenizacao():
    assert ce.juntar_linhas(["pré-", "carga do sistema"], HIF) == "pré-carga do sistema"
    assert ce.juntar_linhas(["especifi-", "cação"], HIF) == "especificação"
    assert ce.juntar_linhas(["anti-", "inflamatório"], ce.Hifenizacao("")) == "anti-inflamatório"
    assert ce.juntar_linhas(["anti-", "aprisionamento"], ce.Hifenizacao("")) == "antiaprisionamento"
    assert ce.juntar_linhas(["considera-", "se"], ce.Hifenizacao("")) == "considera-se"


def test_linha_capa_e_kind():
    m = list(ce.RE_NUM_CAPA.finditer("(21) BR 112018067333-1 A2\n(21) PI 0617329-4 Bl"))
    assert [(x.group(2).replace(" ", ""), x.group(3), ce._kind(x.group(4))) for x in m] == \
        [("112018067333", "1", "A2"), ("0617329", "4", "B1")]
    assert ce.formatar_id({"prefixo": "BR", "numero": "112018067333", "dv": "1", "kind": "A2"}, "completo") \
        == "BR1120180673331-A2"


def test_numero_base_dedup():
    assert dd.numero_base("BR112018067333_RPI2505_1.3.pdf") == "112018067333"
    assert dd.numero_base("BR1020160107504A2.pdf") == "102016010750"
    assert dd.numero_base("BRPI0608116B1.pdf") == "0608116"
    assert dd.numero_base("PI0617329_RPI2505.pdf") == "0617329"


if __name__ == "__main__":
    for nome, f in list(globals().items()):
        if nome.startswith("test_"):
            f(); print("ok", nome)
