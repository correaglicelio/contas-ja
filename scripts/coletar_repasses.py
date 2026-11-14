"""
Contas Já - dinheiro que o governo federal repassou aos municípios (Portal da Transparência, CGU).
Só biblioteca padrão do Python.

Fonte: arquivo mensal de transferências (um .zip por mês, sem chave de acesso):
  https://portaldatransparencia.gov.br/download-de-dados/transferencias/AAAAMM

Regras:
  - "Prefeitura" = linhas cujo favorecido é a Administração Pública Municipal (FPM, Fundeb, SUS, merenda...).
  - "Outros" = demais favorecidos naquele município (associações de escola, entidades, órgãos).
  - Comparação sempre dos MESMOS meses (janeiro até o último mês COMPLETO) em cada ano.
  - O Portal publica os meses aos poucos: o mês corrente e, às vezes, o anterior chegam incompletos.
    Mês "completo" = soma nacional de pelo menos 60% da mediana dos 6 meses anteriores. Os demais ficam em
    meta["pendentes"] e nunca entram na comparação.

Gera public/data/repasses/ (valores em reais inteiros):
  <código UF>.json  {"uf", "m": {ibge: {"n": nome, "t": [prefeitura por mês], "o": [outros por mês],
                                         "f": {código função: [por mês]}, "a": {código ação: [período ano anterior, período ano atual]}}}}
  brasil.json       {"uf": {sigla: {"t": [...], "f": {...}}}}
  meta.json         meses, mês de referência, nomes de funções e ações, avisos

Uso: python scripts/coletar_repasses.py --uf MT | --uf todas      (sempre lê o Brasil inteiro; --uf escolhe o que grava)
"""
from __future__ import annotations

import csv
import io
import sys
import time
import zipfile
from collections import defaultdict
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import comum as c  # noqa: E402

URL = "https://portaldatransparencia.gov.br/download-de-dados/transferencias/{ref}"
SAIDA = c.DADOS_PUBLICOS / "repasses"
ANO_INICIAL = 2023
FAV_PREF = "Administração Pública Municipal"
COLS = ("TIPO FAVORECIDO", "UF", "NOME MUNICÍPIO", "CÓDIGO FUNÇÃO", "NOME FUNÇÃO", "AÇÃO", "NOME AÇÃO", "VALOR TRANSFERIDO")


def valor(txt: str) -> float:
    try:
        return float(txt.strip().replace(".", "").replace(",", "."))       # 1.234,56 -> 1234.56
    except ValueError:
        return 0.0


def meses_ate_hoje(hoje: date) -> list[str]:
    return [f"{a}{m:02d}" for a in range(ANO_INICIAL, hoje.year + 1) for m in range(1, 13) if (a, m) <= (hoje.year, hoje.month)]


def baixar(ref: str):
    with c.abrir(URL.format(ref=ref), timeout=300) as r:
        return zipfile.ZipFile(io.BytesIO(r.read()))


def titulo(txt: str) -> str:
    """O Portal grava as ações em MAIÚSCULAS sem acento: deixa legível (sem inventar acentos)."""
    t = txt.strip().capitalize()
    for sg in ("FPM", "FUNDEB", "ITR", "PNAE", "PDDE", "SUS", "CF", "ART", "PNATE", "FNDE"):
        t = t.replace(sg.capitalize(), sg)
    return t


def main() -> None:
    ufs = c.ufs_escolhidas("MT")
    SAIDA.mkdir(parents=True, exist_ok=True)
    hoje = date.today()
    refs = meses_ate_hoje(hoje)
    n = len(refs)
    c.log(f"Contas Já - repasses federais ({refs[0]} a {refs[-1]})")
    casa = c.CasadorMunicipios()
    mun = c.ler_json(c.DADOS_PUBLICOS / "municipios.json", {}) or {}
    nomes = {m[0]: m[1] for m in mun.get("m", [])}

    T = defaultdict(lambda: [0.0] * n)
    O = defaultdict(lambda: [0.0] * n)
    F = defaultdict(lambda: defaultdict(lambda: [0.0] * n))
    ANO_A0 = hoje.year - 2
    A = defaultdict(lambda: defaultdict(lambda: [0.0] * 36))     # só os 3 últimos anos, por mês
    FUNC, ACAO = {}, {}
    UF_T = defaultdict(lambda: [0.0] * n)
    UF_F = defaultdict(lambda: defaultdict(lambda: [0.0] * n))
    falhou, linhas_lidas, sem_mun = [], 0, 0

    def processar(i: int, ref: str) -> bool:
        nonlocal linhas_lidas, sem_mun
        try:
            z = baixar(ref)
        except Exception as e:  # noqa: BLE001
            c.log(f"   {ref}: sem arquivo ({str(e)[:60]})")
            return False
        ano, mes = int(ref[:4]), int(ref[4:])
        with z.open(z.namelist()[0]) as f:
            leitor = csv.reader(io.TextIOWrapper(f, encoding="latin-1"), delimiter=";")
            cab = next(leitor)
            ix = {k: cab.index(k) for k in COLS}
            qtd = 0
            for lin in leitor:
                v = valor(lin[ix["VALOR TRANSFERIDO"]])
                if not v:
                    continue
                uf, nm = lin[ix["UF"]].strip(), lin[ix["NOME MUNICÍPIO"]].strip()
                if uf not in c.SIGLAS or not nm or nm.lower().startswith("sem informa"):
                    sem_mun += 1
                    continue
                cod = casa.codigo(nm, uf)
                if not cod:
                    sem_mun += 1
                    continue
                qtd += 1
                if lin[ix["TIPO FAVORECIDO"]].strip() != FAV_PREF:
                    O[cod][i] += v
                    continue
                fid, acao = lin[ix["CÓDIGO FUNÇÃO"]].strip(), lin[ix["AÇÃO"]].strip()
                FUNC.setdefault(fid, lin[ix["NOME FUNÇÃO"]].strip())
                ACAO.setdefault(acao, titulo(lin[ix["NOME AÇÃO"]]))
                T[cod][i] += v
                F[cod][fid][i] += v
                UF_T[uf][i] += v
                UF_F[uf][fid][i] += v
                if ano >= ANO_A0:
                    A[cod][acao][(ano - ANO_A0) * 12 + mes - 1] += v
            linhas_lidas += qtd
        c.log(f"   {ref}: {qtd:,} linhas")
        return True

    for i, ref in enumerate(refs):
        if not processar(i, ref):
            falhou.append((i, ref))
        time.sleep(4)          # o Portal bloqueia (erro 405) quando os pedidos vêm em sequência rápida
    for rodada in (1, 2, 3):
        if not [x for x in falhou if x[1] != refs[-1]]:
            break
        falhou = [x for x in falhou if x[1] != refs[-1]] + [x for x in falhou if x[1] == refs[-1]]
        c.log(f"   tentando de novo ({rodada}): {', '.join(r for _, r in falhou if r != refs[-1])}")
        time.sleep(60 * rodada)
        resto = []
        for i, ref in falhou:
            if ref == refs[-1]:
                resto.append((i, ref))   # mês corrente sem arquivo: não vale esperar
                continue
            if not processar(i, ref):
                resto.append((i, ref))
            time.sleep(10)
        falhou = resto
    # o mês corrente pode ainda não ter arquivo (isso não é falha); qualquer outro mês faltando impede a gravação
    graves = [r for _, r in falhou if r != refs[-1]]
    if graves:
        raise RuntimeError(f"Meses sem arquivo: {graves}. Mantendo os dados anteriores.")
    total_mes = [sum(v[i] for v in UF_T.values()) for i in range(n)]
    ultimo_ok = None
    for i in range(n - 1, -1, -1):
        base = sorted(total_mes[max(0, i - 6):i])
        if not base or total_mes[i] >= 0.6 * base[len(base) // 2]:
            ultimo_ok = i
            break
    if ultimo_ok is None:
        raise RuntimeError("Nenhum mês completo encontrado")
    ref_ano, ref_mes = int(refs[ultimo_ok][:4]), int(refs[ultimo_ok][4:])
    ano_prev = ref_ano - 1
    pendentes = [f"{r[:4]}-{r[4:]}" for r in refs[ultimo_ok + 1:]]
    c.log(f"   último mês completo: {ref_mes:02d}/{ref_ano}; pendentes: {pendentes}")
    if casa.faltando:
        c.log(f"   nomes sem código IBGE ({len(casa.faltando)}): {sorted(casa.faltando)[:12]}")
    c.log(f"   linhas aproveitadas: {linhas_lidas:,}; sem município: {sem_mun:,}")

    inteiro = lambda v: [int(round(x)) for x in v]
    gerado = c.agora_iso()
    for uf in ufs:
        cod_uf = c.SIGLAS[uf]
        saida = {}
        for cod in sorted(set(T) | set(O)):
            if cod // 100000 != cod_uf:
                continue
            reg = {"n": nomes.get(cod, str(cod)), "t": inteiro(T[cod]) if cod in T else [0] * n,
                   "o": inteiro(O[cod]) if cod in O else [0] * n}
            f = {fid: inteiro(v) for fid, v in F[cod].items() if any(v)} if cod in F else {}
            if f:
                reg["f"] = f
            a = {}
            for ac, x in (A[cod].items() if cod in A else []):
                pa = sum(x[(ano_prev - ANO_A0) * 12:(ano_prev - ANO_A0) * 12 + ref_mes])
                at = sum(x[(ref_ano - ANO_A0) * 12:(ref_ano - ANO_A0) * 12 + ref_mes])
                if pa or at:
                    a[ac] = [int(round(pa)), int(round(at))]
            if a:
                reg["a"] = a
            saida[str(cod)] = reg
        c.salvar_json(SAIDA / f"{cod_uf}.json", {"uf": uf, "m": saida})
        c.log(f"   {uf}: {len(saida)} municípios")
    brasil = {"uf": {sg: {"t": inteiro(UF_T[sg]), "f": {fid: inteiro(v) for fid, v in UF_F[sg].items() if any(v)}} for sg in UF_T}}
    c.salvar_json(SAIDA / "brasil.json", brasil)
    meta_ant = c.ler_json(SAIDA / "meta.json", {}) or {}
    ufs_disp = sorted(set(meta_ant.get("ufs", [])) | set(ufs))
    c.salvar_json(SAIDA / "meta.json", {
        "coletado_em": gerado, "meses": [f"{r[:4]}-{r[4:]}" for r in refs], "ref_ano": ref_ano, "ref_mes": ref_mes,
        "pendentes": pendentes,
        "ufs": ufs_disp, "funcoes": FUNC, "acoes": {k: v for k, v in ACAO.items()},
        "fonte": "Portal da Transparência (CGU), transferências do governo federal"})
    c.resumo_github([f"Repasses federais: {refs[0]} a {refs[-1]}, comparação jan a {ref_mes:02d}/{ref_ano}, pendentes {pendentes}, UFs {', '.join(ufs)}"])


if __name__ == "__main__":
    main()
