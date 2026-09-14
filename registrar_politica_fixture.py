"""REGISTRO PRE-JOGO ANCORADO NO FIXTURE - MODO OBSERVACAO (driver temporario).

Motivo: os nomes destes confrontos sao AMBIGUOS na busca textual da
API-Football (ex.: dois "Boca Juniors", tres "Valencia", U23/U21 de
Schalke/Leon). O CLI resolve por nome; aqui o fluxo EXATO do comando
`prejogoop --politica` (src/app.py cmd_prejogoop) e replicado com a
entrada ANCORADA no fixture conhecido da API (IDs vindos de /fixtures,
revalidados por fixture_teams contra /teams - regra 12 de identidade).

Nenhum modulo de src/ e alterado; nenhuma formula/threshold muda; o
registro usa a MESMA versao oficial "prejogo-politica-3.2-observacao".
Uso: python registrar_politica_fixture.py <fixture_id> [<fixture_id> ...]
"""
from __future__ import annotations

import io
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                             errors="replace")

from src.api_client import APIFootballClient
from src.config import require_api_key, DEFAULT_LAST_N
from src.fixtures import get_fixture_by_id
from src.h2h import get_h2h_analysis
from src.identity import fixture_teams
from src.match_stats import fetch_team_history
from src.analysis import _league_average_for_fixture
from src.cartoes import (
    avaliar_cartoes_prejogo, league_cards_average_for_fixture)
from src.cobertura import (
    avaliar_cobertura_pre, filtrar_avaliacoes_por_cobertura,
    formatar_relatorio_cobertura, status_da_familia, STATUS_PERMITIDO)
from src.prejogo_opportunity import (
    _benchmark_gols_liga, avaliar_pregame, registrar_aprovadas_prejogo)
from src.resultado import avaliar_resultado_prejogo
from src.policy import (
    LIGAS_PRIORITARIAS, classificar_liga, jogo_elegivel, selecionar_melhor)
from src.politica_operacional import odd_justa_avaliacao
from src.registry import RegistroRecomendacoes


def registrar(client, fixture_id: int) -> None:
    fixture = get_fixture_by_id(client, fixture_id)
    if fixture is None:
        print(f"[FATO] fixture {fixture_id}: NAO ENCONTRADO NA API")
        return
    if fixture.is_finished or fixture.is_live:
        print(f"[FATO] fixture {fixture_id}: jogo iniciado/encerrado "
              f"(status {fixture.status}) - pre-jogo exige jogo nao iniciado")
        return

    # IDENTIDADE: IDs e nomes vem do fixture, revalidados contra /teams
    home_t, away_t = fixture_teams(client, fixture)

    classe, motivo_liga = classificar_liga(
        fixture.league_id, fixture.league_name)
    elegivel, motivo = jogo_elegivel(
        fixture.league_id, fixture.league_name,
        fixture.home_team_name, fixture.away_team_name, dados="SIM")
    out = [f"[FATO] fixture {fixture.fixture_id}: "
           f"{fixture.home_team_name} (id {home_t.id}) x "
           f"{fixture.away_team_name} (id {away_t.id}) | "
           f"{fixture.league_name} (id {fixture.league_id}) => {classe} "
           f"({motivo_liga}) | identidade validada contra /teams"]
    if not elegivel:
        print("\n".join(out + [f"[POLITICA] JOGO INELEGIVEL: {motivo}"]))
        return

    # MESMO fluxo do scan CLI: historicos + benchmarks + h2h(10)
    games_home, un_home = fetch_team_history(
        client, fixture.home_team_id, DEFAULT_LAST_N,
        team_name=fixture.home_team_name)
    games_away, un_away = fetch_team_history(
        client, fixture.away_team_id, DEFAULT_LAST_N,
        team_name=fixture.away_team_name)
    hist = {
        "games_home": games_home,
        "games_away": games_away,
        "n_home": len(games_home),
        "n_away": len(games_away),
        "sem_estatisticas_home": un_home,
        "sem_estatisticas_away": un_away,
    }
    bench_esc = _league_average_for_fixture(client, fixture)
    bench_gols = _benchmark_gols_liga(client, fixture)
    bench_cartoes = league_cards_average_for_fixture(client, fixture)
    h2h = get_h2h_analysis(
        client, fixture.home_team_id, fixture.away_team_id, last=10)
    h2h_n = getattr(h2h, "with_stats", 0) or 0

    # comparador COMPLETO (igual --politica): gols/escanteios + RESULTADO
    # (1X2/DC/DNB/AH) + cartoes, em igualdade
    avaliacoes = avaliar_pregame(hist, bench_esc, bench_gols, h2h_n)
    avaliacoes = avaliacoes + avaliar_resultado_prejogo(
        hist, bench_gols, h2h_n)
    avaliacoes = avaliacoes + avaliar_cartoes_prejogo(
        hist, bench_cartoes, h2h_n=h2h_n)

    veredictos = avaliar_cobertura_pre(
        fixture.league_id, fixture.league_name, hist)
    avaliacoes, bloqueadas = filtrar_avaliacoes_por_cobertura(
        avaliacoes, veredictos)
    out.extend(formatar_relatorio_cobertura(veredictos))
    permitidas = [av for av in avaliacoes
                  if status_da_familia(veredictos, av.mercado)
                  == STATUS_PERMITIDO]
    if bloqueadas:
        out.append(f"[COBERTURA] mercados bloqueados fora da analise: "
                   f"{len(bloqueadas)} linhas (classe D/E ou dado essencial "
                   f"ausente - ausencia nunca vira zero)")
    if not permitidas:
        print("\n".join(out + [
            "[COBERTURA] JOGO REPROVADO: nenhum mercado com cobertura "
            "PERMITIDA para este confronto"]))
        return

    selecao = selecionar_melhor(
        permitidas, hist, bench_esc, bench_gols, bench_cartoes)
    if selecao.reprovacao:
        print("\n".join(out + [
            f"[POLITICA] JOGO REPROVADO: {selecao.reprovacao}"]))
        return

    melhor = selecao.melhor
    sel_op = getattr(selecao, "operacional", None)
    u_op = getattr(sel_op, "util_operacional", None) if sel_op else None
    oj_melhor = odd_justa_avaliacao(melhor.avaliacao)
    oj_txt = (f"{oj_melhor:.2f}" if oj_melhor is not None
              else "NAO CALCULAVEL - DADOS INSUFICIENTES")
    om_txt = ""
    if u_op is not None and u_op.odd_minima is not None:
        om_txt = f" | odd minima aceitavel {u_op.odd_minima:.2f}"

    registros = registrar_aprovadas_prejogo(
        RegistroRecomendacoes(), [melhor.avaliacao], fixture, hist,
        versao="prejogo-politica-3.2-observacao",
    )
    out.append(
        f"[POLITICA] MELHOR OPORTUNIDADE DO JOGO (1 por jogo): "
        f"{melhor.avaliacao.linha} | prob {melhor.avaliacao.prob:.2%} | "
        f"confianca {melhor.avaliacao.confianca:.2f} | odd justa "
        f"{oj_txt}{om_txt} | score de politica {melhor.score} | "
        f"congelada no registro: {registros}")
    if sel_op is not None and getattr(sel_op, "motivo_operacional", None):
        out.append(f"[POLITICA-OPERACIONAL] motivo da linha principal: "
                   f"{sel_op.motivo_operacional}")
    out.append(
        f"[CALCULO] comparacao de mercados: {len(selecao.comparadas)} "
        "linhas disciplinadas; componentes da vencedora: utilidade_prob "
        f"{melhor.utilidade_prob} | utilidade_largura "
        f"{melhor.utilidade_largura} | estabilidade {melhor.estabilidade} "
        f"| aderencia {melhor.aderencia} | cobertura {melhor.cobertura} "
        f"| contracoes {melhor.contracoes}")
    print("\n".join(out))


if __name__ == "__main__":
    require_api_key()
    client = APIFootballClient()
    print(f"[POLITICA] versao do universo: ligas fortes "
          f"({len(LIGAS_PRIORITARIAS)} prioritarias)")
    for arg in sys.argv[1:]:
        registrar(client, int(arg))
        print()