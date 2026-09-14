"""MODO OBSERVACAO PRE-JOGO - produtor de recomendacoes para calibracao.

TESTE DE CALIBRACAO: produz e congela recomendacoes pre-jogo ANTES do
jogo comecar, no momento em que sao produzidas (nunca depois de
conhecer o resultado). Nenhuma logica ja validada e alterada:

    - pre_match_analysis e usada COMO ESTA (identidade validada,
      fixture reancorado, media da liga pela competicao inteira);
    - baselines reutilizam as MESMAS funcoes do motor ao vivo
      (_baseline_escanteios/_baseline_gols: cruzamento casa/fora);
    - combinacao com o benchmark da liga usa os MESMOS pesos (65%
      times + 35% liga). Diferenca rotulada: pre-jogo nao tem estado
      de jogo que desloque a projecao (o live tem: gols ja marcados,
      escanteios ja cobrados), entao as linhas avaliadas sao um LEQUE
      ao redor da projecao (equilibrio +- 4) - sem isso toda linha
      ficaria ~50/50 e nada entraria na janela de aprovacao;
    - probabilidade: Poisson sobre os 90 minutos completos
      (poisson_ge/poisson_le, as mesmas funcoes do live);
    - aprovacao: as MESMAS constantes do motor live (janela de
      probabilidade PROB_MIN_APROVAR..PROB_MAX_APROVAR, confianca
      minima CONF_MIN_TOP1), no maximo 2 linhas por jogo.

Diferenca para o live (novo, rotulada): a CONFIANCA pre-jogo
(_confianca_prejogo) nao tem dados live - usa amostra historica,
benchmark da competicao e H2H com peso MENOR (mesma regra da secao 3).

Cada APROVADA e congelada no REGISTRO DE VALIDACAO com tipo "prejogo"
(minuto/placar NULL: pre-jogo nao tem estado de jogo). Liquidacao
somente depois do encerramento, validada na API (src/settlement.py).
Este modulo NAO aposta: e observacao para medir calibracao.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from src.config import DEFAULT_LAST_N
from src.exceptions import UserFacingError
from src.fixtures import FINISHED_STATUS
from src.live import now_brt
from src.live_opportunity import (
    CONF_MIN_TOP1,
    PROB_MAX_APROVAR,
    PROB_MIN_APROVAR,
    _baseline_escanteios,
    _baseline_gols,
    poisson_ge,
    poisson_le,
)
from src.match_stats import fetch_team_history

# Versao/tipo da analise que produz a observacao pre-jogo
VERSAO_PREJOGO_OP = "prejogo-op-1.0-observacao"

# Disciplina igual a do live: no maximo 2 linhas aprovadas por jogo
MAX_APROVADAS_PREJOGO = 2

# Leque de linhas X.5 ao redor da projecao (equilibrio +- 4)
LEQUE_LINHAS = 4

NENHUMA_MSG = "NENHUMA OPORTUNIDADE PRE-JOGO APROVADA AGORA."


def _linhas_pregame(lam: float) -> list[float]:
    """Leque de linhas X.5 ao redor da projecao pre-jogo.

    O live avalia o equilibrio da projecao +- 1 porque o ESTADO do jogo
    (gols/escanteios ja ocorridos) desloca a probabilidade das linhas.
    Pre-jogo nao ha estado: no equilibrio toda linha fica ~50/50 e nada
    entraria na janela de aprovacao. O leque permite observar as linhas
    de fato na janela 70%-97% (ex.: over de linha baixa, under de linha
    alta) - exatamente o alvo do teste de calibracao (as previsoes de
    ~90% estao calibradas?).
    """
    eq = math.floor(lam) + 0.5
    return sorted({l for l in (eq + k * 1.0 for k in range(-LEQUE_LINHAS,
                                                           LEQUE_LINHAS + 1))
                   if l > 0})


# ----------------------------------------------------------------------
# Confianca pre-jogo (v1 - rotulada; sem dados live por definicao)
# ----------------------------------------------------------------------
def _confianca_prejogo(
    n_min: int,
    benchmark_validas: int | None,
    h2h_n: int,
) -> tuple[float, dict[str, float]]:
    """Confianca [0,1] com os dados DISPONIVEIS antes do jogo.

    Componentes: amostra historica, benchmark da competicao e H2H
    recente com peso MENOR (regra da secao 3 - o h2h e apenas um
    reforco, nunca o sustento principal).
    """
    amostra = min((n_min or 0) / 10.0, 1.0)
    if benchmark_validas is None:
        bench = 0.0
    elif benchmark_validas >= 30:
        bench = 1.0
    else:
        bench = 0.6
    h2h = min((h2h_n or 0) / 5.0, 1.0)

    componentes = {
        "amostra_historica": round(amostra, 2),
        "benchmark_competicao": round(bench, 2),
        "h2h_recente_peso_menor": round(h2h, 2),
    }
    pesos = {
        "amostra_historica": 0.40,
        "benchmark_competicao": 0.40,
        "h2h_recente_peso_menor": 0.20,
    }
    conf = sum(pesos[k] * componentes[k] for k in componentes)
    return round(min(conf, 1.0), 3), componentes


# ----------------------------------------------------------------------
# Avaliacao pre-jogo (Poisson sobre 90 minutos completos)
# ----------------------------------------------------------------------
@dataclass
class AvaliacaoPre:
    """Uma linha over/under avaliada ANTES do jogo comecar."""

    mercado: str
    linha: str
    prob: float
    confianca: float
    conf_componentes: dict[str, float]
    sustentacao: dict[str, Any]
    riscos: list[str] = field(default_factory=list)


@dataclass
class VarreduraPreJogo:
    espec: str
    fixture: Any | None = None
    motivo_sem_jogo: str | None = None
    historico: dict[str, Any] = field(default_factory=dict)
    benchmark_escanteios: dict[str, Any] | None = None
    benchmark_gols: dict[str, Any] | None = None
    benchmark_cartoes: dict[str, Any] | None = None
    avaliacoes: list[AvaliacaoPre] = field(default_factory=list)
    aprovadas: list[AvaliacaoPre] = field(default_factory=list)
    # (id, novo) de cada aprovada congelada no registro permanente
    registros: list[tuple[int, bool]] = field(default_factory=list)
    # MATRIZ DE COBERTURA (camada de elegibilidade): veredictos
    # GOALS/CORNERS/CARDS do PRE_GAME deste fixture e as avaliacoes
    # retiradas do fluxo por mercado bloqueado (D/E ou dado ausente)
    cobertura: list[Any] = field(default_factory=list)
    bloqueadas_cobertura: list[AvaliacaoPre] = field(default_factory=list)


def avaliar_pregame(
    hist: dict[str, Any],
    benchmark_escanteios: dict[str, Any] | None,
    benchmark_gols: dict[str, Any] | None,
    h2h_n: int = 0,
) -> list[AvaliacaoPre]:
    """Avalia linhas over/under de escanteios e gols ANTES do jogo.

    O baseline final e o mesmo do motor live: cruzamento casa/fora dos
    dois times (peso maior) + benchmark da liga (peso menor); sem
    nenhum dos dois => a familia NAO e avaliada (nunca se inventa).
    A probabilidade e Poisson sobre os 90 minutos completos.
    """
    ns = [v for v in (hist.get("n_home"), hist.get("n_away")) if v]
    n_min = min(ns) if ns else 0

    familias: list[tuple[str, tuple[float | None, str], dict | None]] = [
        ("escanteios", _baseline_escanteios(hist), benchmark_escanteios),
        ("gols", _baseline_gols(hist), benchmark_gols),
    ]

    out: list[AvaliacaoPre] = []
    for mercado, (baseline, detalhe), bench in familias:
        league_mean = (bench or {}).get("describe", {}).get("media")
        validas = (bench or {}).get("partidas_validas")

        if baseline is not None and league_mean is not None:
            lam = 0.65 * baseline + 0.35 * league_mean
            det_base = (
                f"{detalhe}; benchmark da liga {round(league_mean, 2)} "
                "(65% times + 35% liga)"
            )
        elif baseline is not None:
            lam, det_base = baseline, detalhe
        elif league_mean is not None:
            lam, det_base = (
                league_mean,
                "apenas benchmark da liga (sem medias dos times)",
            )
        else:
            continue  # sem sustentacao: NAO avalia (nunca inventa)

        conf, comps = _confianca_prejogo(n_min, validas, h2h_n)
        riscos: list[str] = []
        if baseline is None:
            riscos.append(
                "sem medias dos times no historico: apenas benchmark da liga"
            )
        if league_mean is None:
            riscos.append(
                "sem benchmark da liga: apenas historico dos times"
            )
        if n_min < 10:
            riscos.append(
                f"amostra historica pequena: {n_min} jogos (minimo por time)"
            )

        for line in _linhas_pregame(lam):
            for direcao in ("Over", "Under"):
                if direcao == "Over":
                    prob = poisson_ge(int(math.floor(line)) + 1, lam)
                else:
                    prob = poisson_le(int(math.floor(line)), lam)
                out.append(
                    AvaliacaoPre(
                        mercado=mercado,
                        linha=(
                            f"{direcao} {line} {mercado} (total do jogo)"
                        ),
                        prob=round(prob, 4),
                        confianca=conf,
                        conf_componentes=comps,
                        sustentacao={
                            "baseline_pre_jogo": det_base,
                            "lambda_por90": round(lam, 2),
                            "modelo": (
                                "Poisson sobre 90 minutos completos "
                                "(CALCULO, nao dado da API)"
                            ),
                        },
                        riscos=list(riscos),
                    )
                )
    return out


def aprovar_pregame(
    avaliacoes: list[AvaliacaoPre],
) -> list[AvaliacaoPre]:
    """Mesma disciplina do live: janela de probabilidade, confianca
    minima do TOP 1 e no maximo 2 linhas por jogo (prob decrescente)."""
    dentro = [
        a for a in avaliacoes
        if PROB_MIN_APROVAR <= a.prob <= PROB_MAX_APROVAR
        and a.confianca >= CONF_MIN_TOP1
    ]
    dentro.sort(key=lambda a: (-a.prob, -a.confianca))
    return dentro[:MAX_APROVADAS_PREJOGO]


# ----------------------------------------------------------------------
# Congelamento no REGISTRO DE VALIDACAO (tipo prejogo)
# ----------------------------------------------------------------------
def registrar_aprovadas_prejogo(
    reg: Any,
    aprovadas: list[AvaliacaoPre],
    fixture: Any,
    hist: dict[str, Any],
    versao: str = VERSAO_PREJOGO_OP,
) -> list[tuple[int, bool]]:
    """Congela as aprovadas ANTES do jogo. Dedupe: a MESMA observacao
    reexecutada nao gera segundo registro (retorna id existente).

    `versao` e opcional e so rotula o registro; o padrao mantem o
    comportamento historico (nenhum calculo depende dele)."""
    ts = now_brt().strftime("%d/%m/%Y %H:%M:%S")
    ns = [v for v in (hist.get("n_home"), hist.get("n_away")) if v]
    amostra_n = min(ns) if ns else None

    ja_registradas = {
        (r["mercado"], r["linha"], r["probabilidade"])
        for r in reg.listar(fixture_id=fixture.fixture_id)
        if r["tipo"] == "prejogo"
    }

    registros: list[tuple[int, bool]] = []
    for av in aprovadas:
        chave = (av.mercado, av.linha, av.prob)
        if chave in ja_registradas:
            existente = next(
                r["id"] for r in reg.listar(fixture_id=fixture.fixture_id)
                if r["tipo"] == "prejogo"
                and (r["mercado"], r["linha"], r["probabilidade"]) == chave
            )
            registros.append((existente, False))
            continue
        rec_id = reg.registrar(
            fixture_id=fixture.fixture_id,
            tipo="prejogo",
            mercado=av.mercado,
            linha=av.linha,
            probabilidade=av.prob,
            versao_analise=versao,
            competicao=fixture.league_name,
            mandante=fixture.home_team_name,
            visitante=fixture.away_team_name,
            confianca=av.confianca,
            amostra_n=amostra_n,
            dados_favoraveis=[
                av.sustentacao["baseline_pre_jogo"],
                av.sustentacao["modelo"],
            ],
            contradicoes_riscos=(av.riscos or None),
            snapshot_api_ts=ts,
        )
        registros.append((rec_id, True))
    return registros


# ----------------------------------------------------------------------
# Benchmark de gols da liga (mesma consulta do benchmark de escanteios
# => cache hit quando pre_match_analysis ja buscou a liga)
# ----------------------------------------------------------------------
def _benchmark_gols_liga(client: Any, fixture: Any) -> dict[str, Any] | None:
    if fixture.league_id is None or fixture.season is None:
        return None
    from src.config import DEFAULT_TIMEZONE

    try:
        fixtures = client.get(
            "/fixtures",
            params={
                "league": fixture.league_id,
                "season": fixture.season,
                "timezone": DEFAULT_TIMEZONE,
            },
        )
    except UserFacingError:
        return None
    totais = [
        float(f["goals"]["home"] + f["goals"]["away"])
        for f in fixtures
        if f["fixture"]["status"]["short"] in FINISHED_STATUS
        and f["goals"]["home"] is not None
        and f["goals"]["away"] is not None
    ]
    if not totais:
        return None
    from src.stats import describe

    return {
        "liga": fixture.league_name,
        "partidas_validas": len(totais),
        "describe": describe(totais),
    }


# ----------------------------------------------------------------------
# Varredura completa de UM jogo (entrada: "Time A x Time B")
# ----------------------------------------------------------------------
def scan_pregame_opportunities(
    client: Any, espec: str, registrar: bool = True,
    incluir_resultado: bool = False,
    incluir_cartoes: bool = False,
) -> VarreduraPreJogo:
    """Observacao pre-jogo de UM confronto. Reusa pre_match_analysis
    (identidade validada + fixture + media da liga) COMO ESTA; depois
    avalia linhas, aprova com a disciplina do live e congela.

    `incluir_resultado` (default False: o fluxo legado gols/escanteios
    permanece EXATAMENTE como era) acrescenta a familia RESULTADO
    (src/resultado.py: 1X2, Dupla Chance, DNB, AH - bloco EXPERIMENTAL
    EM OBSERVACAO, aprovacao tecnica 07/09/2026; aguarda validacao
    estatistica) a lista de avaliacoes - usada pela POLITICA para
    comparar todos os mercados em igualdade. Nenhum calculo de
    gols/escanteios muda; as avaliacoes de resultado vem do proprio
    bloco e carregam o marcador RISCO_STATUS_RESULTADO.

    `incluir_cartoes` (default False: fluxo legado preservado) acrescenta
    o TOTAL de cartoes pre-jogo do bloco validado (src/cartoes.py) com o
    SEU benchmark real da competicao (league_cards_average: TODAS as
    finalizadas da temporada, exclusoes contabilizadas). Nenhuma
    matematica de cartoes e alterada aqui - o bloco e reusado como esta.

    MATRIZ DE COBERTURA (08/09/2026): antes da aprovacao, cada mercado
    passa pela elegibilidade (src/cobertura.py) por COMPETICAO x
    MERCADO x MODO PRE_GAME. Mercado BLOQUEADO (classe D/E ou dado
    essencial ausente no historico real) sai do fluxo inteiro - nunca e
    avaliado, aprovado ou registrado. PERMITIDO e OBSERVACAO seguem;
    quem impede a recomendacao automatica da classe C no fluxo
    operacional (--politica) e o app, nao este scan."""
    from src.analysis import pre_match_analysis
    from src.resolver import split_match_spec

    pair = split_match_spec(espec)
    if pair is None:
        raise UserFacingError(
            'Especifique o jogo como "Time A x Time B".'
        )
    team_a, team_b = pair

    res = pre_match_analysis(client, team_a, team_b)
    fixture = res["proximo_jogo"]
    if fixture is None:
        return VarreduraPreJogo(
            espec=espec,
            motivo_sem_jogo=(
                "nenhum jogo futuro entre os dois times encontrado na API"
            ),
        )
    if fixture.is_finished:
        return VarreduraPreJogo(
            espec=espec,
            fixture=fixture,
            motivo_sem_jogo=(
                f"jogo JA ENCERRADO (status {fixture.status}); "
                "observacao pre-jogo exige jogo nao iniciado"
            ),
        )
    if fixture.is_live:
        return VarreduraPreJogo(
            espec=espec,
            fixture=fixture,
            motivo_sem_jogo=(
                f"jogo JA EM ANDAMENTO (status {fixture.status}); "
                "observacao pre-jogo exige jogo nao iniciado"
            ),
        )

    # Historicos (mesmos parametros de pre_match_analysis => cache hit)
    games_home, un_home = fetch_team_history(
        client, fixture.home_team_id, DEFAULT_LAST_N,
        team_name=fixture.home_team_name,
    )
    games_away, un_away = fetch_team_history(
        client, fixture.away_team_id, DEFAULT_LAST_N,
        team_name=fixture.away_team_name,
    )
    hist = {
        "games_home": games_home,
        "games_away": games_away,
        "n_home": len(games_home),
        "n_away": len(games_away),
        "sem_estatisticas_home": un_home,
        "sem_estatisticas_away": un_away,
    }

    bench_esc = res["media_liga"]
    bench_gols = _benchmark_gols_liga(client, fixture)
    # Benchmark real de cartoes da MESMA competicao/temporada (bloco
    # validado); falha de consulta => None e a familia avalia com risco
    # declarado (nunca se inventa benchmark, nunca se usa o de gols).
    bench_cartoes = None
    if incluir_cartoes:
        from src.cartoes import league_cards_average_for_fixture

        bench_cartoes = league_cards_average_for_fixture(client, fixture)
    h2h_n = getattr(res["h2h"], "with_stats", 0) or 0

    avaliacoes = avaliar_pregame(hist, bench_esc, bench_gols, h2h_n)
    if incluir_resultado:
        # Familia RESULTADO (bloco EXPERIMENTAL EM OBSERVACAO, aprovacao
        # tecnica 07/09/2026; aguarda validacao estatistica): entra na
        # MESMA lista para a comparacao de mercados da politica. Nada
        # aqui recalcula gols/escanteios - o bloco tem motor proprio e
        # ja marca cada avaliacao com RISCO_STATUS_RESULTADO.
        from src.resultado import avaliar_resultado_prejogo

        avaliacoes = avaliacoes + avaliar_resultado_prejogo(
            hist, bench_gols, h2h_n
        )
    if incluir_cartoes:
        # TOTAL DE CARTOES validado (bloco aprovado 07/09/2026): entra
        # na MESMA lista em igualdade - reusado integralmente, sem
        # preferencia e sem recalcular nada dos demais mercados.
        from src.cartoes import avaliar_cartoes_prejogo

        avaliacoes = avaliacoes + avaliar_cartoes_prejogo(
            hist, bench_cartoes, h2h_n=h2h_n
        )

    # MATRIZ DE COBERTURA (ETAPA 9 do fluxo pre-jogo): a elegibilidade
    # por mercado e decidida ANTES da aprovacao - mercado BLOQUEADO
    # (classe D/E da auditoria ou dado essencial ausente no fixture)
    # nao gera avaliacao, aprovacao nem registro. Ausencia de dado
    # NUNCA e convertida em zero. Classes PERMITIDAS e OBSERVACAO
    # seguem (observacao e o modo de calibracao deste scan).
    from src.cobertura import (
        avaliar_cobertura_pre,
        filtrar_avaliacoes_por_cobertura,
    )

    veredictos = avaliar_cobertura_pre(
        fixture.league_id, fixture.league_name, hist)
    avaliacoes, bloqueadas = filtrar_avaliacoes_por_cobertura(
        avaliacoes, veredictos)

    aprovadas = aprovar_pregame(avaliacoes)

    varredura = VarreduraPreJogo(
        espec=espec,
        fixture=fixture,
        historico=hist,
        benchmark_escanteios=bench_esc,
        benchmark_gols=bench_gols,
        benchmark_cartoes=bench_cartoes,
        avaliacoes=avaliacoes,
        aprovadas=aprovadas,
        cobertura=veredictos,
        bloqueadas_cobertura=[av for av, _v in bloqueadas],
    )
    if registrar and aprovadas:
        from src.registry import RegistroRecomendacoes

        try:
            varredura.registros = registrar_aprovadas_prejogo(
                RegistroRecomendacoes(), aprovadas, fixture, hist
            )
        except Exception:
            from src.logging_config import get_logger

            get_logger().exception(
                "Registro de validacao pre-jogo falhou (fixture %s)",
                fixture.fixture_id,
            )
    return varredura