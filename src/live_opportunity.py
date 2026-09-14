"""ETAPA 2.2 - OPORTUNIDADES DE APOSTA AO VIVO (modulo novo).

REGRA CENTRAL: analise ao vivo = PRE-JOGO + ESTADO ATUAL DO JOGO +
MINUTO + HISTORICO + MUNDO/CASA-FORA + BENCHMARK DA COMPETICAO +
LINHA/ODD AO VIVO REAL (quando disponivel). Nunca recomendar apenas
porque o jogo parece movimentado.

Nada do pre-jogo, da resolucao de identidade, do cache historico ou do
modulo live (ETAPA 2) e alterado: este modulo apenas CONSUME essas
camadas.

Saida separada em [FATO] / [CALCULO] / [INTERPRETACAO]. Sem
sustentacao suficiente => "NENHUMA OPORTUNIDADE AO VIVO APROVADA AGORA."
(nunca se forca uma selecao).

Fluxo da varredura ("Existe alguma oportunidade ao vivo agora?") -
TRIAGEM GLOBAL: TODOS os jogos ao vivo elegiveis sao avaliados
superficialmente antes de qualquer limite:
    1. lista os jogos REALMENTE em andamento e aplica cortes baratos
       da propria listagem (status/minuto);
    2. TRIAGEM BARATA de TODOS os elegiveis: um snapshot por jogo
       (leitura cacheada, TTL de live), lendo status/minuto, placar,
       disponibilidade de estatisticas, quantidade de dados live,
       competencia, historico em cache e sinal basico de volume;
    3. score de triagem com JANELA DE VALOR 30'-75': um jogo aos 45'
       disputa de igual para igual com jogos mais adiantados (nunca
       descartado "apenas porque existem jogos mais avancados");
    4. SOMENTE ENTAO o limite de deep dive (max. 2) e aplicado, com o
       score da triagem informado no motivo do descarte;
    5. refresh forcado do snapshot + deep dive apenas nos escolhidos;
    6. auditoria obrigatoria antes de aprovar;
    7. aprova NO MAXIMO 2 oportunidades, de jogos DIFERENTES
       (TOP 2 so se tambem for forte; nunca segunda selecao apenas
       para formar multipla).

Modelo de probabilidade ([CALCULO], rotulado como calculo - nunca
dado da API): taxa combinada = (1-w)*taxa_historica + w*taxa_observada
(w cresce com o minuto, teto 50%: o ritmo por minuto e apenas UM dos
indicadores), e probabilidade via cauda de Poisson SOBRE O TEMPO
RESTANTE - nunca extrapolacao linear ingenua. Over 9.5 com 6 atuais
aos 20' e totalmente diferente de aos 82'.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from src.api_client import APIFootballClient
from src.cobertura import (
    STATUS_OBSERVACAO,
    avaliar_cobertura_live,
    familias_para_deep_dive,
    familias_permitidas,
    resumo_cobertura,
    status_da_familia,
)
from src.config import DEFAULT_TIMEZONE
from src.exceptions import IdentityDivergenceError, UserFacingError
from src.handicap import implied_prob, overround_ok, real_line
from src.h2h import get_h2h_analysis, h2h_corner_stats
from src.live import (
    LIVE_IN_PLAY,
    LiveSnapshot,
    fetch_live_snapshot,
    find_live_fixture_id,
    list_live_games,
    now_brt,
)
from src.match_stats import fetch_team_history
from src.odds import (
    SEM_ODD_LIVE,
    FixtureOdds,
    mercado_ft_total,
    parse_ah_value,
    parse_total_value,
)
from src.stats import compute_team_stats, describe

# ----------------------------------------------------------------------
# Parametros da varredura e dos limiares de aprovacao (explicitos)
# ----------------------------------------------------------------------
MIN_MINUTE = 15            # corte de triagem: jogo muito recente
MAX_DEEP = 2               # deep dives por varredura (limite aplicado
                           # DEPOIS da triagem global de TODOS os elegiveis)
PROB_MIN_APROVAR = 0.70    # probabilidade estimada minima
PROB_MAX_APROVAR = 0.97    # acima disso a linha ja esta praticamente decidida
CONF_MIN_TOP1 = 0.60       # confianca minima do TOP 1
CONF_MIN_TOP2 = 0.68       # TOP 2 so se tambem for forte
EDGE_MINIMO = 0.03         # margem minima sobre a probabilidade implicita
AUDIT_FRESHNESS_SEG = 180  # snapshot precisa ser recente (3 min)
ODD_LIVE_MAX_IDADE_SEG = 900  # odd live mais antiga que 15 min: nao e "atual"

NENHUMA_MSG = "NENHUMA OPORTUNIDADE AO VIVO APROVADA AGORA."

# Status cujo mercado de 90 minutos nao se aplica
_STATUS_INAPLICAVEL = {"ET", "BT", "P"}


# ----------------------------------------------------------------------
# Calculos puros: Poisson sobre o tempo restante
# ----------------------------------------------------------------------
def poisson_ge(need: int, lam: float) -> float:
    """P(X >= need) para X ~ Poisson(lam); need <= 0 => 1.0."""
    if need <= 0:
        return 1.0
    if lam <= 0:
        return 0.0
    if need > 60:
        return 0.0
    term = math.exp(-lam) * lam**need / math.factorial(need)
    total = term
    k = need
    while term > 1e-14 and k < need + 200:
        k += 1
        term = term * lam / k
        total += term
    return min(total, 1.0)


def poisson_le(max_k: int, lam: float) -> float:
    """P(X <= max_k); max_k < 0 => 0.0 (estado atual ja decidiu a linha)."""
    if max_k < 0:
        return 0.0
    return 1.0 - poisson_ge(max_k + 1, lam)


def poisson_pmf(k: int, lam: float) -> float:
    if lam <= 0:
        return 1.0 if k == 0 else 0.0
    if k < 0:
        return 0.0
    return math.exp(-lam) * lam**k / math.factorial(k)


@dataclass
class RateBlend:
    """Taxa combinada historico + observado (por 90 minutos)."""

    per90: float
    baseline_per90: float | None
    observado_per90: float | None
    w_observado: float
    minuto: int | None
    detalhe: str = ""


def blend_rate(
    baseline_per90: float | None,
    observado_total: int | float | None,
    elapsed: int | None,
) -> RateBlend:
    """Combina a taxa historica (por 90) com a taxa observada no jogo.

    O peso do observado cresce com o minuto (mais jogo = mais
    informacao do estado REAL), piso 20% e teto 50%: o historico nunca
    e ignorado e o ritmo por minuto e apenas UM dos indicadores.
    """
    if baseline_per90 is None:
        baseline_per90 = 0.0
    if elapsed is None or elapsed <= 0 or observado_total is None:
        return RateBlend(
            per90=baseline_per90,
            baseline_per90=baseline_per90 if baseline_per90 else None,
            observado_per90=None,
            w_observado=0.0,
            minuto=elapsed,
            detalhe="sem minuto/observacao: apenas taxa historica",
        )
    observado_per90 = float(observado_total) * 90.0 / elapsed
    w = max(0.20, min(elapsed / 90.0, 0.50))
    blended = (1 - w) * baseline_per90 + w * observado_per90
    detalhe = (
        f"taxa combinada por 90 = (1-{round(w, 2)})*{round(baseline_per90, 2)} "
        f"(historico) + {round(w, 2)}*{round(observado_per90, 2)} (observado)"
    )
    return RateBlend(
        per90=blended,
        baseline_per90=baseline_per90 if baseline_per90 else None,
        observado_per90=round(observado_per90, 2),
        w_observado=round(w, 2),
        minuto=elapsed,
        detalhe=detalhe,
    )


def tempo_restante(status: str, elapsed: int | None) -> int | None:
    """Minutos restantes de jogo regulamentar (90') pelo estado real.

    Prorrogacao e descartada na triagem (mercados de 90' apenas).
    """
    if elapsed is None:
        return None
    if status == "HT":
        return 45
    return max(90 - elapsed, 1)


# ----------------------------------------------------------------------
# Estruturas de resultado
# ----------------------------------------------------------------------
@dataclass
class Descartado:
    jogo: str
    fixture_id: int
    minuto: int | None
    mercado: str
    motivo: str


@dataclass
class OddUsada:
    """Odd REALMENTE ao vivo usada na avaliacao (ou None).

    atual=False quando o timestamp da fonte esta fora da janela de
    frescor: nesse caso a odd e exibida com seu timestamp mas NAO e
    apresentada como preco atual para fins de aprovacao (regra 6).
    """

    bookmaker: str
    mercado_feed: str
    value_feed: str
    odd: float
    update: str
    implied: float
    atual: bool = True


@dataclass
class Avaliacao:
    jogo: str
    fixture_id: int
    competicao: str
    minuto: int | None
    status: str
    placar: str
    mercado: str            # escanteios / gols / cartoes / resultado
    linha: str             # ex.: "Over 9.5 escanteios (total do jogo)"
    prob: float            # probabilidade estimada [0,1] (CALCULO)
    sustentacao: dict[str, Any] = field(default_factory=dict)
    confianca: float = 0.0
    conf_componentes: dict[str, float] = field(default_factory=dict)
    riscos: list[str] = field(default_factory=list)
    odd: OddUsada | None = None
    odds_live_existentes: bool = False
    auditoria: list[tuple[str, bool, str]] = field(default_factory=list)
    aprovada: bool = False
    rejeicao: str | None = None

    @property
    def edge(self) -> float | None:
        """Margem sobre a probabilidade implicita (so com odd atual)."""
        if self.odd is None or not self.odd.atual:
            return None
        return self.prob - self.odd.implied

    @property
    def classificacao(self) -> str:
        """Com odd real e atual: oportunidade com preco. Sem odd ao vivo
        real: apenas OPORTUNIDADE ESTATISTICA (regra da secao 6)."""
        if self.odd is not None and self.odd.atual:
            return "OPORTUNIDADE COM ODD AO VIVO REAL"
        return "OPORTUNIDADE ESTATISTICA AO VIVO"


@dataclass
class Candidato:
    snapshot: LiveSnapshot
    historico: dict[str, Any] = field(default_factory=dict)
    benchmark_escanteios: dict[str, Any] | None = None
    benchmark_gols: dict[str, Any] | None = None
    benchmark_cartoes: dict[str, Any] | None = None
    h2h_stats: dict[str, Any] | None = None
    h2h_n: int = 0
    odds: FixtureOdds | None = None
    odds_observacao: str = ""
    avaliacoes: list[Avaliacao] = field(default_factory=list)
    avisos: list[str] = field(default_factory=list)
    auditoria_candidato: list[tuple[str, bool, str]] = field(default_factory=list)
    # MATRIZ DE COBERTURA (camada de elegibilidade): veredictos
    # GOALS/CORNERS/CARDS desta leitura (None em nada foi convertido)
    cobertura: list[Any] = field(default_factory=list)


@dataclass
class Varredura:
    hora: str
    total_ao_vivo: int
    total_elegiveis: int = 0     # passaram nos cortes baratos da listagem
    total_triados: int = 0       # TODOS os elegiveis: passaram pela
                                 # triagem barata (um snapshot cada)
    sondados: list[dict[str, Any]] = field(default_factory=list)
    descartados: list[Descartado] = field(default_factory=list)
    candidatos: list[Candidato] = field(default_factory=list)
    aprovadas: list[Avaliacao] = field(default_factory=list)
    # MATRIZ DE COBERTURA: veredictos por fixture_id (leitura do deep
    # dive, a mais fresca) e aprovadas de classe C - OBSERVACAO nunca
    # vira recomendacao automatica (somente exibicao/calibracao)
    cobertura: dict[int, list[Any]] = field(default_factory=dict)
    observacao: list[Avaliacao] = field(default_factory=list)
    # filtro de familias de mercado do operador (None = todas; regra
    # valida: apenas restringe QUais familias o deep dive avalia)
    mercados: tuple[str, ...] | None = None

    @property
    def mensagem_nenhuma(self) -> bool:
        return not self.aprovadas


# ----------------------------------------------------------------------
# Estagio 1: cortes baratos a partir da listagem
# ----------------------------------------------------------------------
def _motivo_estagio1(game: Any) -> str | None:
    status = game.status
    if status in _STATUS_INAPLICAVEL:
        return "jogo em prorrogacao/penaltis: mercados de 90 minutos nao aplicaveis"
    if status == "SUSP":
        return "jogo suspenso na fonte nesta leitura"
    if game.elapsed is None:
        return "minuto nao informado na fonte"
    if game.elapsed < MIN_MINUTE:
        return f"minuto {game.elapsed}' < {MIN_MINUTE}': jogo muito recente"
    if status not in {"1H", "HT", "2H"}:
        return f"status {status}: fora da janela de jogo regulamentar analisavel"
    return None


def _historico_em_cache(client: APIFootballClient, team_id: int) -> bool:
    """Historico do time ja coletado antes? Consulta LOCAL ao cache de
    requisicoes (nenhuma chamada a API): se os ultimos 10 jogos do time
    ja estao cacheados, o deep dive dele nao gasta requisicoes."""
    cache = getattr(client, "cache", None)
    if cache is None:
        return False
    try:
        return bool(
            cache.get(
                "/fixtures",
                {"team": team_id, "last": 10, "timezone": DEFAULT_TIMEZONE},
            )
        )
    except Exception:
        return False


def _triagem_global_score(snapshot: LiveSnapshot, client=None) -> float:
    """Score da TRIAGEM GLOBAL (barata: usa so o snapshot + cache local).

    NUNCA e dominado pelo minuto mais avancado. Componentes (0-11):

      (0-4) maturidade com JANELA DE VALOR: plena entre 30' e 75' - jogo
            com dados suficientes E tempo restante para existir mercado.
            Rampa 15-30'; decaimento 75-90' (quase sem tempo restante,
            menos valor de mercado). Um jogo aos 45' pode valer MAIS que
            um jogo aos 88' - e esse e o objetivo da correcao.
      (0-3) riqueza dos dados live: escanteios, finalizacoes e posse
            presentes nos dois lados (1 ponto cada).
      (0-1) blocos reais de 1o/2o tempo na fonte.
      (0-1) competencia identificada (league_id + temporada: benchmark
            da liga possivel).
      (0-1) historico dos dois times ja em cache (consulta local).
      (0-1) sinal basico de mercado: volume real de escanteios +
            finalizacoes ate o minuto (sinal FRACO de pre-filtro; a
            recomendacao real exige o deep dive completo - nunca se
            aprova um jogo "so porque parece movimentado").
    """
    snap = snapshot
    score = 0.0

    # (1) maturidade: janela de valor 30-75'
    elapsed = snap.elapsed or 0
    if 30 <= elapsed <= 75:
        score += 4.0
    elif 15 <= elapsed < 30:
        score += 4.0 * (elapsed - 15) / 15.0
    elif elapsed > 75:
        score += max(0.0, 4.0 * (90 - elapsed) / 15.0)

    # (2) riqueza dos dados live (ambos os lados)
    for stat_type in ("Corner Kicks", "Total Shots", "Ball Possession"):
        if stat_type in snap.stats_home and stat_type in snap.stats_away:
            score += 1.0

    # (3) blocos reais de tempo
    if snap.stats_1h or snap.stats_2h:
        score += 1.0

    # (4) competencia identificada (benchmark possivel)
    if snap.league_id is not None and snap.season is not None:
        score += 1.0

    # (5) historico ja em cache (sem gastar requisicao)
    if client is not None and _historico_em_cache(
        client, snap.home_team_id
    ) and _historico_em_cache(client, snap.away_team_id):
        score += 1.0

    # (6) sinal basico de mercado (volume real ate agora; sinal fraco)
    corners = snap.stats_home.get("Corner Kicks"), snap.stats_away.get("Corner Kicks")
    shots = snap.stats_home.get("Total Shots"), snap.stats_away.get("Total Shots")
    volume = sum(v for v in corners + shots if isinstance(v, (int, float)))
    score += min(volume / 30.0, 1.0)

    return round(score, 2)


# ----------------------------------------------------------------------
# Coleta do deep dive (reusa as camadas validadas do pre-jogo)
# ----------------------------------------------------------------------
def _historico_teams(client: APIFootballClient, snap: LiveSnapshot) -> dict[str, Any]:
    """Ultimos 10 jogos de cada time. Os IDs vem do PROPRIO fixture e ja
    foram validados no fetch_live_snapshot (regra de identidade); o
    fetch_team_history revalida ID + nome antes de coletar."""
    games_home, un_home = fetch_team_history(
        client, snap.home_team_id, last=10, team_name=snap.home_team_name
    )
    games_away, un_away = fetch_team_history(
        client, snap.away_team_id, last=10, team_name=snap.away_team_name
    )
    return {
        "games_home": games_home,
        "games_away": games_away,
        "stats_home": compute_team_stats(games_home) if games_home else None,
        "stats_away": compute_team_stats(games_away) if games_away else None,
        "n_home": len(games_home),
        "n_away": len(games_away),
        "sem_estatisticas_home": un_home,
        "sem_estatisticas_away": un_away,
    }


def _media(seq: list[float]) -> float | None:
    seq = [v for v in seq if v is not None]
    if not seq:
        return None
    return sum(seq) / len(seq)


def _taxa_cruzada(games_side: list[Any], lado: str) -> dict[str, Any]:
    """Medias por jogo com MUNDO casa-fora (lado do fixture); amostra
    geral apenas quando a sub-amostra do lado tem menos de 4 jogos."""
    no_lado = [g for g in games_side if (lado == "casa") == g.played_at_home]
    amostra = no_lado if len(no_lado) >= 4 else games_side

    def campo(nome: str) -> float | None:
        return _media([getattr(g, nome, None) for g in amostra])

    return {
        "n_no_mando": len(no_lado),
        "n_usados": len(amostra),
        "gols_pro": campo("goals_for"),
        "gols_contra": campo("goals_against"),
        "esc_pro": campo("corners_for"),
        "esc_contra": campo("corners_against"),
    }


def _baseline_escanteios(hist: dict[str, Any]) -> tuple[float | None, str]:
    """Total esperado de escanteios pelo cruzamento casa/fora dos times."""
    th = _taxa_cruzada(hist.get("games_home") or [], "casa")
    ta = _taxa_cruzada(hist.get("games_away") or [], "fora")
    if None in (th["esc_pro"], th["esc_contra"], ta["esc_pro"], ta["esc_contra"]):
        return None, "sem medias de escanteios no historico"
    exp_home = (th["esc_pro"] + ta["esc_contra"]) / 2
    exp_away = (ta["esc_pro"] + th["esc_contra"]) / 2
    return exp_home + exp_away, (
        f"cruzamento casa/fora (n mandante {th['n_usados']}, n visitante "
        f"{ta['n_usados']}): mandante {round(exp_home, 2)} + visitante "
        f"{round(exp_away, 2)} escanteios"
    )


def _baseline_gols(hist: dict[str, Any]) -> tuple[float | None, str]:
    th = _taxa_cruzada(hist.get("games_home") or [], "casa")
    ta = _taxa_cruzada(hist.get("games_away") or [], "fora")
    if None in (th["gols_pro"], th["gols_contra"], ta["gols_pro"], ta["gols_contra"]):
        return None, "sem medias de gols no historico"
    exp_home = (th["gols_pro"] + ta["gols_contra"]) / 2
    exp_away = (ta["gols_pro"] + th["gols_contra"]) / 2
    return exp_home + exp_away, (
        f"cruzamento casa/fora: mandante {round(exp_home, 2)} + visitante "
        f"{round(exp_away, 2)} gols"
    )


def _benchmark_gols(
    client: APIFootballClient, snap: LiveSnapshot
) -> dict[str, Any] | None:
    """Media de gols da competicao INTEIRA na temporada (todos os jogos
    encerrados; placares vem do proprio /fixtures - sem estatistica por
    partida). Mesma consulta do benchmark de escanteios => cache hit."""
    if snap.league_id is None or snap.season is None:
        return None
    try:
        fixtures = client.get(
            "/fixtures",
            params={
                "league": snap.league_id,
                "season": snap.season,
                "timezone": DEFAULT_TIMEZONE,
            },
        )
    except UserFacingError:
        return None
    totais = [
        float(f["goals"]["home"] + f["goals"]["away"])
        for f in fixtures
        if f["fixture"]["status"]["short"] in {"FT", "AET", "PEN"}
        and f["goals"]["home"] is not None
        and f["goals"]["away"] is not None
    ]
    if not totais:
        return None
    return {
        "liga": snap.league_name,
        "liga_id": snap.league_id,
        "temporada": snap.season,
        "partidas_validas": len(totais),
        "describe": describe(totais),
    }


def _benchmark_escanteios(
    client: APIFootballClient, snap: LiveSnapshot
) -> dict[str, Any] | None:
    """Media de escanteios da competicao INTEIRA (regra de integridade:
    TODAS as partidas finalizadas da temporada do fixture; partida sem
    escanteios na fonte e excluida e contabilizada - nunca zerada)."""
    if snap.league_id is None or snap.season is None:
        return None
    try:
        from src.analysis import league_corner_average

        bench = league_corner_average(
            client,
            league_id=snap.league_id,
            season=snap.season,
            league_name=snap.league_name,
        )
    except UserFacingError:  # ex.: limite de requisicoes atingido
        return None
    if bench and bench.get("partidas_validas", 0) > 0:
        return bench
    return None


def _h2h_peso_menor(
    client: APIFootballClient, snap: LiveSnapshot
) -> tuple[dict[str, Any] | None, int]:
    """H2H recente (peso MENOR no raciocinio - regra da secao 3)."""
    try:
        result = get_h2h_analysis(
            client, snap.home_team_id, snap.away_team_id, last=5
        )
        return h2h_corner_stats(result), result.with_stats
    except UserFacingError:
        return None, 0


# ----------------------------------------------------------------------
# Confianca (secao 8)
# ----------------------------------------------------------------------
def _confianca(
    snapshot: LiveSnapshot,
    hist: dict[str, Any],
    benchmark: dict[str, Any] | None,
    rate: RateBlend,
    ja_decidida: bool,
) -> tuple[float, dict[str, float]]:
    """Confianca [0,1]: minuto/tempo restante, placar (via prob), amostra
    historica, estabilidade (coerencia historico x observado), mando,
    qualidade das estatisticas live, dados ausentes e eventos que
    alteram o jogo (expulsao)."""
    known = ("Corner Kicks", "Total Shots", "Ball Possession")
    qualidade = sum(
        1 for t in known if t in snapshot.stats_home and t in snapshot.stats_away
    ) / len(known)

    n_min = min(hist.get("n_home", 0), hist.get("n_away", 0))
    amostra = min(n_min / 10.0, 1.0)

    if benchmark and benchmark.get("partidas_validas", 0) >= 30:
        bench = 1.0
    elif benchmark:
        bench = 0.6
    else:
        bench = 0.0

    elapsed = snapshot.elapsed or 0
    maturidade = 1.0 if ja_decidida else 0.4 + 0.6 * min(elapsed / 75.0, 1.0)

    # coerencia: divergencia forte entre historico e observado reduz a
    # confianca em QUALQUER projecao (a projecao pre-jogo nao sobrevive
    # intacta a mudanca estrutural no jogo)
    if (
        rate.baseline_per90
        and rate.observado_per90 is not None
        and rate.baseline_per90 > 0
    ):
        r = max(rate.observado_per90, 0.01) / rate.baseline_per90
        coerencia = max(0.0, min(1.0, 1.0 - abs(math.log(r))))
    else:
        coerencia = 0.5

    vermelhos = 0
    for lado in (snapshot.stats_home, snapshot.stats_away):
        try:
            vermelhos += int(lado.get("Red Cards") or 0)
        except (TypeError, ValueError):
            pass
    estrutural = 0.0 if vermelhos else 1.0

    componentes = {
        "qualidade_dados_live": round(qualidade, 2),
        "amostra_historica": round(amostra, 2),
        "benchmark_competicao": bench,
        "maturidade_do_minuto": round(maturidade, 2),
        "coerencia_historico_x_observado": round(coerencia, 2),
        "sem_expulsao_estrutural": estrutural,
    }
    pesos = {
        "qualidade_dados_live": 0.25,
        "amostra_historica": 0.20,
        "benchmark_competicao": 0.15,
        "maturidade_do_minuto": 0.15,
        "coerencia_historico_x_observado": 0.15,
        "sem_expulsao_estrutural": 0.10,
    }
    conf = sum(pesos[k] * v for k, v in componentes.items())
    return round(min(conf, 1.0), 3), componentes


# ----------------------------------------------------------------------
# Avaliacao de linhas de total (over/under) com o tempo restante
# ----------------------------------------------------------------------
def _linhas_estatisticas(proj_total: float) -> list[float]:
    """Linhas X.5 ao redor da projecao final: equilibrio e vizinhas."""
    eq = math.floor(proj_total) + 0.5
    return sorted({eq, eq + 1.0, eq - 1.0})


def _avaliar_total(
    candidato: Candidato,
    mercado: str,
    unidade: str,
    atual: int | None,
    baseline: float | None,
    detalhe_baseline: str,
    league_mean: float | None,
    bench_para_confianca: dict[str, Any] | None,
    periodos: str = "total do jogo",
    tempo_restante_min: int | None = None,
    riscos_extra: list[str] | None = None,
) -> list[Avaliacao]:
    """Linhas over/under de uma familia de total via Poisson no restante.

    O baseline final cruza times (peso maior) com o benchmark da liga
    (peso menor); a taxa observada no jogo entra com peso crescente.
    """
    snap = candidato.snapshot
    out: list[Avaliacao] = []
    if atual is None:
        return out
    restante = (
        tempo_restante_min
        if tempo_restante_min is not None
        else tempo_restante(snap.status, snap.elapsed)
    )
    if restante is None:
        return out

    if baseline is not None and league_mean is not None:
        base_final = 0.65 * baseline + 0.35 * league_mean
        det_base = (
            f"{detalhe_baseline}; benchmark da liga {round(league_mean, 2)} "
            "(65% times + 35% liga)"
        )
    elif baseline is not None:
        base_final, det_base = baseline, detalhe_baseline
    elif league_mean is not None:
        base_final, det_base = (
            league_mean, "apenas benchmark da liga (sem medias dos times)"
        )
    else:
        return out  # sem sustentacao historica: NAO avalia (nunca inventa)

    rate = blend_rate(base_final, atual, snap.elapsed)
    esperado_restante = rate.per90 * restante / 90.0
    proj_final = atual + esperado_restante

    for line in _linhas_estatisticas(proj_final):
        for direcao in ("Over", "Under"):
            if direcao == "Over":
                need = int(math.floor(line)) + 1 - atual
                prob = poisson_ge(need, esperado_restante)
                ja_decidida = need <= 0
            else:
                max_a_mais = int(math.floor(line)) - atual
                prob = poisson_le(max_a_mais, esperado_restante)
                ja_decidida = max_a_mais < 0
            ja_decidida = ja_decidida or prob >= 0.999 or prob <= 0.001

            conf, comps = _confianca(
                snap, candidato.historico, bench_para_confianca,
                rate, ja_decidida,
            )
            riscos = list(riscos_extra or [])
            if rate.observado_per90 is None:
                riscos.append("taxa observada indisponivel; apenas historico")
            if ja_decidida:
                riscos.append("linha ja decidida pelo estado atual do jogo")

            out.append(
                Avaliacao(
                    jogo=f"{snap.home_team_name} x {snap.away_team_name}",
                    fixture_id=snap.fixture_id,
                    competicao=f"{snap.league_name} ({snap.country})",
                    minuto=snap.elapsed,
                    status=snap.status,
                    placar=f"{snap.goals_home}-{snap.goals_away}",
                    mercado=mercado,
                    linha=f"{direcao} {line} {unidade} ({periodos})",
                    prob=round(prob, 4),
                    sustentacao={
                        "atual_no_jogo": atual,
                        "minuto": snap.elapsed,
                        "tempo_restante_min": restante,
                        "baseline": det_base,
                        "taxa_combinada_por90": round(rate.per90, 2),
                        "peso_do_observado": rate.w_observado,
                        "esperado_no_restante": round(esperado_restante, 2),
                        "projecao_final": round(proj_final, 2),
                        "modelo": (
                            "Poisson sobre o tempo restante "
                            "(CALCULO, nao dado da API)"
                        ),
                    },
                    confianca=conf,
                    conf_componentes=comps,
                    riscos=riscos,
                    odds_live_existentes=candidato.odds is not None,
                )
            )
    return out


def _avaliar_cartoes_live(candidato: Candidato) -> None:
    """Linhas over/under do TOTAL de cartoes AO VIVO, em PONTOS pela
    convencao validada (amarelo=1, vermelho=2 - declarada na linha).

    Reusa integralmente o bloco pre-jogo APROVADO (src/cartoes.py:
    baseline_cartoes cruzado casa/fora em pontos + benchmark real da
    liga em benchmark_cartoes) e o MESMO motor live das demais familias
    (_avaliar_total: blend com o ritmo observado + Poisson sobre o
    tempo restante). Exige amarelos E vermelhos de AMBOS os lados na
    fonte: componente ausente adia a familia (nunca vira zero - mesma
    disciplina da liquidacao). O feed de odds nao tem convencao de
    contagem declaravel para cartoes: a linha permanece OPORTUNIDADE
    ESTATISTICA AO VIVO (odd/bookmaker None, risco declarado).
    """
    snap = candidato.snapshot
    amarelos_h = snap.stats_home.get("Yellow Cards")
    amarelos_a = snap.stats_away.get("Yellow Cards")
    vermelhos_h = snap.stats_home.get("Red Cards")
    vermelhos_a = snap.stats_away.get("Red Cards")
    if None in (amarelos_h, amarelos_a, vermelhos_h, vermelhos_a):
        candidato.avisos.append(
            "cartoes ao vivo incompletos na fonte para esta partida "
            "(exigidos amarelos e vermelhos de ambos os lados)"
        )
        return

    from src.cartoes import UNIDADE_CARTOES, baseline_cartoes

    base = baseline_cartoes(candidato.historico)
    base_v = base["total"] if base is not None else None
    base_det = (
        base["detalhe"] if base is not None
        else "sem medias de cartoes no historico"
    )
    bench_y = candidato.benchmark_cartoes
    bench_y_media = (bench_y or {}).get("describe", {}).get("media")
    atual_cards = amarelos_h + amarelos_a + 2 * (vermelhos_h + vermelhos_a)

    riscos_cartoes = [
        "convencao de contagem da casa nao informada pela fonte; "
        "estimativa assume amarelo=1, vermelho=2",
        "estatistica de arbitro nao fornecida pela fonte "
        "(API-Football v3); nunca inventada",
    ]
    if bench_y is None:
        riscos_cartoes.append(
            "benchmark de cartoes da liga indisponivel nesta leitura"
        )
    novas = _avaliar_total(
        candidato, "cartoes", UNIDADE_CARTOES, atual_cards,
        base_v, base_det, bench_y_media, bench_y,
        riscos_extra=riscos_cartoes,
    )

    # faltas da fonte: CONTEXTO FACTUAL na sustentacao quando a fonte
    # fornece (nunca inventadas; sem modelo faltas-cartoes)
    faltas_h = snap.stats_home.get("Fouls")
    faltas_a = snap.stats_away.get("Fouls")
    if faltas_h is not None and faltas_a is not None:
        for av in novas:
            av.sustentacao["faltas_na_fonte_ate_o_minuto"] = (
                f"mandante {faltas_h} x visitante {faltas_a} "
                f"(total {faltas_h + faltas_a} no minuto {snap.elapsed}; "
                "dado da fonte, sem modelo faltas-cartoes)"
            )
    candidato.avaliacoes.extend(novas)


def _timestamp_recente(update: str) -> bool:
    """Odd live e 'atual' apenas com timestamp dentro da janela de frescor."""
    try:
        ts = datetime.fromisoformat(str(update))
        idade = (datetime.now(ts.tzinfo) - ts).total_seconds()
        return 0 <= idade <= ODD_LIVE_MAX_IDADE_SEG
    except Exception:
        return True  # sem timestamp parseavel: exibe com aviso na auditoria


def _anexar_odd_real(avaliacoes: list[Avaliacao], candidato: Candidato) -> None:
    """Quando a fonte tem linha real AO VIVO igual a linha avaliada,
    anexa a odd (bookmaker/mercado/linha/odd/timestamp + implicita).
    Odd pre-jogo nunca entra aqui: so existe candidato.odds (live).

    Casamento EXATO (regra obrigatoria 08/09/2026): somente o mercado
    FT de TOTAL DO JOGO da familia (ID estavel do feed ou nome
    canonico) + lado + linha identicos. Mercados de 1o/2o tempo,
    total do mandante/visitante e demais escopos NUNCA casam com a
    linha FT de total do jogo.
    """
    odds = candidato.odds
    if odds is None:
        return
    for book in odds.bookmakers:
        update = book.update or odds.update
        for market in book.markets:
            if mercado_ft_total("escanteios", market):
                familia, unidade = "escanteios", "escanteios"
            elif mercado_ft_total("gols", market):
                familia, unidade = "gols", "gols"
            else:
                continue
            for ov in market.values:
                parsed = parse_total_value(ov.value)
                if parsed is None:
                    continue
                direcao, line = parsed
                alvo = f"{direcao} {line} {unidade} (total do jogo)"
                for av in avaliacoes:
                    if av.mercado == familia and av.linha == alvo:
                        av.odd = OddUsada(
                            bookmaker=book.name,
                            mercado_feed=market.name,
                            value_feed=ov.value,
                            odd=round(ov.odd, 3),
                            update=update or "timestamp nao informado",
                            implied=round(implied_prob(ov.odd), 4),
                            atual=_timestamp_recente(update),
                        )


# ----------------------------------------------------------------------
# Mercados de resultado (somente com linha real ao vivo)
# ----------------------------------------------------------------------
def _distribuicao_margem(
    candidato: Candidato,
) -> tuple[dict[int, float], str] | None:
    """Distribuicao do diferencial final de gols via Poisson por lado
    (forca casa/fora dos times + placar atual + tempo restante)."""
    snap = candidato.snapshot
    hist = candidato.historico
    th = _taxa_cruzada(hist.get("games_home") or [], "casa")
    ta = _taxa_cruzada(hist.get("games_away") or [], "fora")
    if None in (th["gols_pro"], th["gols_contra"], ta["gols_pro"], ta["gols_contra"]):
        return None
    base_home = (th["gols_pro"] + ta["gols_contra"]) / 2
    base_away = (ta["gols_pro"] + th["gols_contra"]) / 2
    bench_gols = candidato.benchmark_gols
    if bench_gols:
        media_lado = (bench_gols.get("describe") or {}).get("media")
        if media_lado:
            base_home = 0.7 * base_home + 0.3 * media_lado / 2
            base_away = 0.7 * base_away + 0.3 * media_lado / 2

    gh, ga = snap.goals_home, snap.goals_away
    if gh is None or ga is None or snap.elapsed is None:
        return None
    rate_h = blend_rate(base_home, gh, snap.elapsed)
    rate_a = blend_rate(base_away, ga, snap.elapsed)
    restante = tempo_restante(snap.status, snap.elapsed) or 0
    lam_h = rate_h.per90 * restante / 90.0
    lam_a = rate_a.per90 * restante / 90.0

    dist: dict[int, float] = {}
    for i in range(0, 8):
        for j in range(0, 8):
            d = (gh - ga) + i - j
            dist[d] = dist.get(d, 0.0) + poisson_pmf(i, lam_h) * poisson_pmf(j, lam_a)
    detalhe = (
        f"Poisson por lado com {restante}' restantes: taxas restantes "
        f"{round(lam_h, 2)} (mandante) x {round(lam_a, 2)} (visitante) "
        f"a partir de {gh}-{ga} no minuto {snap.elapsed}"
    )
    return dist, detalhe


def _prob_vitoria_equivalente(dist: dict[int, float], linha: float, lado: str) -> float:
    """P(vitoria integral) + 0.5*P(meia vitoria) da liquidacao do AH."""
    p_win = 0.0
    for diff, p in dist.items():
        margin = diff if lado == "Home" else -diff
        result = margin + linha
        if result > 0.25:
            p_win += p
        elif result == 0.25:
            p_win += 0.5 * p
    return min(p_win, 1.0)


def _avaliacao_resultado(
    candidato: Candidato,
    linha_txt: str,
    prob: float,
    detalhe: str,
    conf: float,
    comps: dict[str, Any],
    riscos: list[str],
    odd_usada: OddUsada | None,
    extra: str = "",
) -> Avaliacao:
    snap = candidato.snapshot
    sust: dict[str, Any] = {
        "distribuicao": detalhe,
        "modelo": "Poisson por lado com placar e tempo restante (CALCULO)",
    }
    if extra:
        sust["convencao"] = extra
    return Avaliacao(
        jogo=f"{snap.home_team_name} x {snap.away_team_name}",
        fixture_id=snap.fixture_id,
        competicao=f"{snap.league_name} ({snap.country})",
        minuto=snap.elapsed,
        status=snap.status,
        placar=f"{snap.goals_home}-{snap.goals_away}",
        mercado="resultado",
        linha=linha_txt,
        prob=round(prob, 4),
        sustentacao=sust,
        confianca=conf,
        conf_componentes=comps,
        riscos=list(riscos),
        odd=odd_usada,
        odds_live_existentes=candidato.odds is not None,
    )


def _odds_resultado_por_linha(candidato: Candidato) -> dict[str, OddUsada]:
    """Mapeia as odds LIVE do feed para as LINHAS CANONICAS da familia
    resultado (texto liquidadavel pelo registro - src/settlement.py).

    So entra odd de /odds/live (candidato.odds; odd pre-jogo nunca existe
    aqui). A correspondencia do AH usa a convencao de sinal VALIDADA:
    feed = perspectiva do mandante, prefixo = lado apostado, Away inverte
    o sinal (real_line). Sem correspondencia exata => a linha fica SEM
    odd (OPORTUNIDADE ESTATISTICA), nunca com odd de outra linha."""
    odds = candidato.odds
    if odds is None:
        return {}
    # pares de dupla chance canonicos: frozenset dos simbolos -> texto
    dc_canonica = {
        frozenset(("1", "X")): "Dupla chance 1X",
        frozenset(("X", "2")): "Dupla chance X2",
        frozenset(("1", "2")): "Dupla chance 12",
    }
    dc_simbolo = {"home": "1", "1": "1", "draw": "X", "x": "X",
                  "away": "2", "2": "2"}
    m1x2 = {"home": "Vitoria mandante (1)", "draw": "Empate (X)",
            "away": "Vitoria visitante (2)"}

    por_linha: dict[str, OddUsada] = {}
    from src.resultado import fmt_ah  # import local (evita circular)
    for book in odds.bookmakers:
        update = book.update or odds.update
        for market in book.markets:
            mname = market.name.lower()
            for ov in market.values:
                odd_usada = OddUsada(
                    bookmaker=book.name,
                    mercado_feed=market.name,
                    value_feed=ov.value,
                    odd=round(ov.odd, 3),
                    update=update or "timestamp nao informado",
                    implied=round(implied_prob(ov.odd), 4),
                    atual=_timestamp_recente(update),
                )
                if "asian handicap" in mname:
                    parsed = parse_ah_value(ov.value)
                    if parsed is None:
                        continue  # sem lado+linha validaveis: nao interpreta
                    lado_feed, feed_line = parsed
                    linha_real = real_line(lado_feed, feed_line)
                    lado_pt = "mandante" if lado_feed == "Home" else "visitante"
                    chave = f"AH {lado_pt} {fmt_ah(linha_real)} (90 minutos)"
                    por_linha.setdefault(chave, odd_usada)
                elif "draw no bet" in mname:
                    lado = ov.value.strip().capitalize()
                    if lado not in ("Home", "Away"):
                        continue
                    lado_pt = "mandante" if lado == "Home" else "visitante"
                    por_linha.setdefault(
                        f"DNB {lado_pt} (empate anula)", odd_usada)
                elif "double chance" in mname:
                    valor = ov.value.strip().lower().replace(" ", "")
                    partes = valor.replace("/", "-").split("-")
                    if (len(partes) != 2 or partes[0] not in dc_simbolo
                            or partes[1] not in dc_simbolo):
                        continue
                    chave = dc_canonica.get(
                        frozenset((dc_simbolo[partes[0]],
                                   dc_simbolo[partes[1]])))
                    if chave:
                        por_linha.setdefault(chave, odd_usada)
                elif "match winner" in mname:
                    chave = m1x2.get(ov.value.strip().lower())
                    if chave:
                        por_linha.setdefault(chave, odd_usada)
    return por_linha


def _avaliar_resultado(candidato: Candidato) -> list[Avaliacao]:
    """Familia RESULTADO ao vivo: 1X2, Dupla Chance, DNB e AH do LEQUE
    CANONICO (src/resultado.py - bloco EXPERIMENTAL EM OBSERVACAO) sobre
    a distribuicao de margem AO VIVO (_distribuicao_margem: placar atual
    + minuto + tempo restante - a probabilidade reflete o ESTADO da
    partida, nunca a pre-jogo).

    Linhas CANONICAS => toda linha produzida e liquidadavel pelo TEXTO
    EXATO congelado no registro (src/settlement.py, mesma fonte de
    verdade do bloco pre-jogo). As probabilidades vem das MESMAS funcoes
    do bloco (prob_1x2/prob_dupla_chance/prob_dnb/prob_ah: convencao
    uniforme, DNB = AH 0.0 mesmo numero; liquidacao validada em
    src/handicap.py) aplicadas a distribuicao live - nada e recalculado
    aqui. Toda avaliacao carrega RISCO_STATUS_RESULTADO (experimental em
    observacao - nao operacional validada).

    Odd LIVE real e anexada SOMENTE quando o feed oferece a MESMA linha
    (convencao de sinal validada real_line); sem odd => a linha concorre
    como OPORTUNIDADE ESTATISTICA (odd/bookmaker permanecem None)."""
    from src.resultado import (  # import local: src.resultado importa
        # deste modulo (evita import circular); funcoes do bloco
        # EXPERIMENTAL EM OBSERVACAO (liquidacao validada em
        # src/handicap.py)
        RISCO_STATUS_RESULTADO,
        fmt_ah,
        linhas_ah_prejogo,
        prob_1x2,
        prob_ah,
        prob_dnb,
        prob_dupla_chance,
    )

    snap = candidato.snapshot
    dist_pack = _distribuicao_margem(candidato)
    if dist_pack is None:
        return []  # sem historico de gols ou placar: NUNCA inventa linha
    dist, detalhe = dist_pack

    conf, comps = _confianca(
        snap, candidato.historico, candidato.benchmark_gols,
        RateBlend(0.0, None, None, 0.0, snap.elapsed, ""), False,
    )
    base_riscos = [
        RISCO_STATUS_RESULTADO,
        "mercado de resultado: modelo Poisson por lado (estimativa)",
        "placar muda o estado a qualquer momento",
    ]
    odd_por_linha = _odds_resultado_por_linha(candidato)

    out: list[Avaliacao] = []

    def _add(linha_txt: str, prob: float, extra: str = "") -> None:
        odd = odd_por_linha.get(linha_txt)
        riscos = list(base_riscos)
        if odd is None:
            riscos.append(
                "sem odd ao vivo real para esta linha: oportunidade "
                "estatistica"
            )
        out.append(_avaliacao_resultado(
            candidato, linha_txt, prob, detalhe, conf, comps, riscos, odd,
            extra=extra,
        ))

    # 1X2 e Dupla Chance (probabilidades diretas da distribuicao live)
    p1, px, p2 = prob_1x2(dist)
    _add("Vitoria mandante (1)", p1)
    _add("Empate (X)", px)
    _add("Vitoria visitante (2)", p2)
    dc = prob_dupla_chance(dist)
    _add("Dupla chance 1X", dc["1X"])
    _add("Dupla chance X2", dc["X2"])
    _add("Dupla chance 12", dc["12"])

    # DNB = AH 0.0 (convencao uniforme do bloco pre-jogo:
    # empate devolve e nao conta; MESMO numero do AH 0.0)
    for lado in ("mandante", "visitante"):
        p = prob_dnb(dist, lado)
        if p is None:
            continue
        _add(
            f"DNB {lado} (empate anula)", p,
            extra=(
                "prob = P(vitoria do lado); empate devolve o stake e "
                "nao conta; equivale a AH 0.0 (mesmo numero, mesmo "
                "mercado)"
            ),
        )

    # Handicap asiatico por lado (leque canonico liquidadavel 0.0 +/-0.25 ... +/-1.5)
    for lado in ("mandante", "visitante"):
        for linha in linhas_ah_prejogo():
            p = prob_ah(dist, lado, linha)
            if p is None:
                continue
            odd = odd_por_linha.get(
                f"AH {lado} {fmt_ah(linha)} (90 minutos)")
            extra = (
                f"convencao validada: feed {odd.value_feed!r} => lado "
                f"{lado}, linha real {fmt_ah(linha)} (perspectiva do "
                "mandante no feed)"
                if odd is not None else
                "convencao validada: linha canonica do leque pre-jogo "
                "(bloco resultado); sem feed para esta linha nesta leitura"
            )
            _add(
                f"AH {lado} {fmt_ah(linha)} (90 minutos)", p, extra=extra,
            )
    return out


# ----------------------------------------------------------------------
# Deep dive de um candidato
# ----------------------------------------------------------------------
def deep_dive(
    client: APIFootballClient,
    snapshot: LiveSnapshot,
    mercados: tuple[str, ...] | None = None,
) -> Candidato:
    """Contexto pre-jogo + estado atual + odds ao vivo de UM candidato.

    Identidade: os IDs das equipes vem do fixture e ja foram validados
    no fetch_live_snapshot; o historico revalida ID + nome antes de
    coletar (divergencia interrompe - regra 12 do CLAUDE.md).

    `mercados` restringe QUAIS familias de mercado sao avaliadas
    (None = todas, comportamento validado). O filtro NAO altera nenhum
    calculo: as mesmas funcoes avaliam as mesmas linhas; familias fora
    do filtro simplesmente nao sao avaliadas nesta varredura.
    """
    candidato = Candidato(snapshot=snapshot)
    snap = snapshot

    # historico individual (ultimos 10 de cada lado, casa/fora)
    try:
        candidato.historico = _historico_teams(client, snap)
    except IdentityDivergenceError:
        # REGRA 12: divergencia ID x nome INTERROMPE - nunca outro clube
        raise
    except UserFacingError as exc:
        candidato.avisos.append(f"historico indisponivel nesta leitura: {exc}")

    # benchmarks da competicao (integridade: temporada do fixture)
    candidato.benchmark_escanteios = _benchmark_escanteios(client, snap)
    candidato.benchmark_gols = _benchmark_gols(client, snap)
    if candidato.benchmark_escanteios is None:
        candidato.avisos.append(
            "benchmark de escanteios da liga indisponivel nesta leitura"
        )

    # benchmark de CARTOES da liga (bloco validado src/cartoes.py): a
    # MESMA lista de /fixtures da competicao e as MESMAS
    # /fixtures/statistics por partida do benchmark de escanteios =>
    # quase todo o custo ja esta em cache. Computado somente quando a
    # familia entra nesta varredura; falha de consulta => None (nunca
    # se inventa benchmark, nunca se usa o de outra familia).
    if mercados is None or "cartoes" in mercados:
        from src.cartoes import league_cards_average_for_fixture

        candidato.benchmark_cartoes = league_cards_average_for_fixture(
            client, snap
        )

    # H2H com peso menor
    candidato.h2h_stats, candidato.h2h_n = _h2h_peso_menor(client, snap)

    # odds AO VIVO reais (endpoint dedicado; nunca odd pre-jogo)
    try:
        from src.odds import fetch_live_odds

        candidato.odds = fetch_live_odds(client, snap.fixture_id, refresh=True)
        candidato.odds_observacao = (
            "odds live fornecidas pela fonte"
            if candidato.odds
            else SEM_ODD_LIVE
        )
    except UserFacingError as exc:
        candidato.odds = None
        candidato.odds_observacao = f"odds live nao consultadas: {exc}"

    hist = candidato.historico

    # ------------------- escanteios (total) -------------------
    corner_h = snap.stats_home.get("Corner Kicks")
    corner_a = snap.stats_away.get("Corner Kicks")
    if (mercados is not None and "escanteios" not in mercados):
        pass  # familia fora do filtro desta varredura
    elif corner_h is not None and corner_a is not None:
        base_c, det_c = _baseline_escanteios(hist)
        bench_c = candidato.benchmark_escanteios
        bench_c_media = (bench_c or {}).get("describe", {}).get("media")
        candidato.avaliacoes.extend(_avaliar_total(
            candidato, "escanteios", "escanteios", corner_h + corner_a,
            base_c, det_c, bench_c_media, bench_c,
        ))
    else:
        candidato.avisos.append(
            "escanteios ao vivo ausentes na fonte para esta partida"
        )

    # --------- escanteios por tempo (1oT/2oT: blocos REAIS) ---------
    if (mercados is not None and "escanteios" not in mercados):
        pass  # familia fora do filtro desta varredura
    elif snap.status == "2H" and snap.stats_2h:
        c2_h = (snap.stats_2h.get(snap.home_team_id) or {}).get("Corner Kicks")
        c2_a = (snap.stats_2h.get(snap.away_team_id) or {}).get("Corner Kicks")
        if c2_h is not None and c2_a is not None:
            amostras_2t = [
                g.corners_for_2nd_half + g.corners_against_2nd_half
                for g in (hist.get("games_home") or []) + (hist.get("games_away") or [])
                if g.corners_for_2nd_half is not None
                and g.corners_against_2nd_half is not None
            ]
            base_2t = _media(amostras_2t)
            if base_2t is not None and len(amostras_2t) >= 3:
                restante = tempo_restante(snap.status, snap.elapsed)
                candidato.avaliacoes.extend(_avaliar_total(
                    candidato, "escanteios", "escanteios", c2_h + c2_a,
                    base_2t,
                    f"media real de escanteios de 2o tempo no historico "
                    f"(n {len(amostras_2t)} meios-tempos)",
                    None, None, periodos="2o tempo",
                    tempo_restante_min=restante,
                ))

    # ------------------- gols (total) -------------------
    gh, ga = snap.goals_home, snap.goals_away
    if (mercados is not None and "gols" not in mercados):
        pass  # familia fora do filtro desta varredura
    elif gh is not None and ga is not None:
        base_g, det_g = _baseline_gols(hist)
        bench_g = candidato.benchmark_gols
        bench_g_media = (bench_g or {}).get("describe", {}).get("media")
        candidato.avaliacoes.extend(_avaliar_total(
            candidato, "gols", "gols", gh + ga, base_g, det_g,
            bench_g_media, bench_g,
            riscos_extra=["gols: placar muda o estado a qualquer momento"],
        ))

    # ------- cartoes: total em PONTOS (bloco validado + estado live) -------
    if (mercados is not None and "cartoes" not in mercados):
        pass  # familia fora do filtro desta varredura
    else:
        _avaliar_cartoes_live(candidato)

    # ------- mercados de resultado (1X2/DC/DNB/AH: leque validado) -------
    if mercados is None or "resultado" in mercados:
        candidato.avaliacoes.extend(_avaliar_resultado(candidato))

    # anexa odds reais as linhas de total correspondentes
    _anexar_odd_real(candidato.avaliacoes, candidato)
    return candidato


# ----------------------------------------------------------------------
# Auditoria obrigatoria (secao 11)
# ----------------------------------------------------------------------
def _auditar_candidato(
    client: APIFootballClient, candidato: Candidato
) -> list[tuple[str, bool, str]]:
    checks: list[tuple[str, bool, str]] = []
    snap = candidato.snapshot

    # 1. o jogo continua ao vivo? (checagem fresca)
    try:
        raw = client.get(
            "/fixtures",
            params={"id": snap.fixture_id, "timezone": DEFAULT_TIMEZONE},
            ttl=60,
        )
        status_now = raw[0]["fixture"]["status"]["short"] if raw else "NA"
        checks.append((
            "jogo continua ao vivo", status_now in LIVE_IN_PLAY,
            f"status real agora: {status_now}",
        ))
        # 4. identidade correta (fixture fresco == snapshot validado)
        if raw:
            names_ok = (
                raw[0]["teams"]["home"]["name"] == snap.home_team_name
                and raw[0]["teams"]["away"]["name"] == snap.away_team_name
            )
            checks.append((
                "identidade correta (IDs do fixture + nomes validados)",
                names_ok,
                f"{snap.home_team_name} (id {snap.home_team_id}) x "
                f"{snap.away_team_name} (id {snap.away_team_id})",
            ))
    except UserFacingError as exc:
        checks.append(("jogo continua ao vivo", False, str(exc)))
        checks.append((
            "identidade correta", False, "fixture nao relido nesta auditoria"
        ))

    # 2. snapshot com timestamp recente
    try:
        coletado = datetime.strptime(snap.collected_at, "%d/%m/%Y %H:%M:%S")
        idade = (now_brt().replace(tzinfo=None) - coletado).total_seconds()
        checks.append((
            "snapshot com timestamp recente",
            0 <= idade <= AUDIT_FRESHNESS_SEG,
            f"leitura de {snap.collected_at} (ha {int(idade)}s)",
        ))
    except (ValueError, TypeError):
        checks.append(("snapshot com timestamp recente", False, "timestamp ilegivel"))

    # 3. minuto e status coerentes
    st, el = snap.status, snap.elapsed
    coerente = (
        (st == "1H" and el is not None and el <= 50)
        or (st == "HT" and el is not None and 44 <= el <= 50)
        or (st == "2H" and el is not None and 45 <= el <= 95)
    )
    checks.append(("minuto e status coerentes", coerente, f"status {st}, minuto {el}"))

    # 5. dado ausente nunca virou zero (None fica FORA do snapshot)
    sem_none = all(
        v is not None
        for lado in (snap.stats_home, snap.stats_away)
        for v in lado.values()
    )
    checks.append((
        "dado ausente nunca virou zero", sem_none,
        "estatistica ausente fica fora do snapshot (s/d); zero exibido e "
        "valor real da fonte",
    ))

    # 9. historico sem partidas posteriores ao jogo
    jogos = list(candidato.historico.get("games_home") or []) + list(
        candidato.historico.get("games_away") or []
    )
    posteriores = [g for g in jogos if g.date[:10] > snap.date_local[:10]]
    checks.append((
        "historico sem partidas posteriores ao jogo",
        not posteriores,
        f"{len(jogos)} jogos verificados; {len(posteriores)} posteriores",
    ))

    # 10. benchmark pertence a competicao do fixture
    bench = candidato.benchmark_escanteios
    bench_ok = bench is None or (
        bench.get("liga_id") == snap.league_id
        and bench.get("temporada") == snap.season
    )
    bench_txt = (
        "benchmark indisponivel nesta leitura (confianca penalizada)"
        if bench is None else
        f"benchmark liga_id {bench.get('liga_id')} temporada "
        f"{bench.get('temporada')}: {bench.get('partidas_validas')} validas / "
        f"{bench.get('partidas_encerradas')} encerradas / "
        f"{bench.get('partidas_sem_escanteios')} excluidas"
    )
    checks.append(("benchmark pertence a competicao", bench_ok, bench_txt))

    # 6. odd pre-jogo nunca usada como live
    odd_ok = (
        all(av.odd is None for av in candidato.avaliacoes)
        or candidato.odds is not None
    )
    checks.append((
        "odd pre-jogo nao usada como live", odd_ok,
        "odds anexadas vem exclusivamente de /odds/live (timestamp "
        "exibido); sem odds live => OPORTUNIDADE ESTATISTICA",
    ))

    return checks


def _auditar_avaliacao(
    av: Avaliacao, candidato: Candidato
) -> list[tuple[str, bool, str]]:
    checks: list[tuple[str, bool, str]] = []

    # 7. linha pertence ao mercado correto
    if av.odd is None:
        checks.append((
            "linha pertence ao mercado correto", True,
            "linha estatistica (feed sem linha real para esta linha)",
        ))
    else:
        checks.append((
            "linha pertence ao mercado correto", True,
            f"feed: {av.odd.mercado_feed} | value real {av.odd.value_feed} "
            f"@ {av.odd.odd} (update {av.odd.update})",
        ))

    # 8. handicap interpretado corretamente (convencao validada + par)
    if av.mercado == "resultado" and av.linha.startswith("AH"):
        conv = av.sustentacao.get("convencao", "")
        conv_ok = "convencao validada" in conv
        par_txt = "par sem odd completa para checagem de overround"
        par_ok: bool | None = None
        if candidato.odds is not None:
            for book in candidato.odds.bookmakers:
                market = book.find_market("asian handicap")
                if market is None:
                    continue
                por_linha: dict[float, list[tuple[str, float]]] = {}
                for ov in market.values:
                    parsed = parse_ah_value(ov.value)
                    if parsed is None:
                        continue
                    lado, linha = parsed
                    por_linha.setdefault(abs(linha), []).append((lado, ov.odd))
                for pares in por_linha.values():
                    lados = {lado for lado, _ in pares}
                    if lados == {"Home", "Away"}:
                        (oh,) = [o for la, o in pares if la == "Home"]
                        (oa,) = [o for la, o in pares if la == "Away"]
                        par_ok = overround_ok(oh, oa)
                        par_txt = (
                            "overround do par Home/Away: ok"
                            if par_ok else "overround do par fora do esperado"
                        )
                break
        ok = conv_ok and par_ok is not False
        checks.append((
            "handicap validado (equipe + lado + linha)", ok,
            f"{conv}; {par_txt}" if conv else par_txt,
        ))
    else:
        checks.append((
            "handicap validado (equipe + lado + linha)", True,
            "nao se aplica (mercado sem handicap)",
        ))

    # 11. nenhum calculo contradiz o estado atual
    atual = av.sustentacao.get("atual_no_jogo")
    coerente = True
    detalhe = "probabilidade consistente com o estado atual"
    try:
        linha_num = float(av.linha.split()[1])
        if (
            av.mercado in ("escanteios", "gols", "cartoes")
            and atual is not None
            and av.linha.startswith("Over")
            and linha_num < float(atual)
        ):
            coerente = av.prob == 1.0
            detalhe = "over ja garantido pelo estado: prob deve ser 100%"
        if (
            av.mercado in ("escanteios", "gols", "cartoes")
            and atual is not None
            and av.linha.startswith("Under")
            and linha_num < float(atual)
        ):
            coerente = av.prob == 0.0
            detalhe = "under ja impossivel pelo estado: prob deve ser 0%"
    except (ValueError, IndexError):
        pass
    checks.append(("calculo coerente com o estado atual", coerente, detalhe))

    # 12. probabilidade com sustentacao explicita
    n_min = min(
        candidato.historico.get("n_home", 0),
        candidato.historico.get("n_away", 0),
    )
    sust_ok = bool(av.sustentacao) and n_min >= 5
    checks.append((
        "probabilidade com sustentacao explicita", sust_ok,
        f"{len(av.sustentacao)} inputs explicitos; n historico minimo "
        f"{n_min} (>= 5 exigido)",
    ))

    return checks


# ----------------------------------------------------------------------
# Ranking e aprovacao (secao 9)
# ----------------------------------------------------------------------
def _aprovar(client: APIFootballClient, candidatos: list[Candidato]) -> list[Avaliacao]:
    """Aprova no maximo 2, do mais sustentado para o menos sustentado,
    com auditoria obrigatoria. TOP 2 somente se tambem for forte e de
    jogo DIFERENTE (nunca segunda selecao apenas para formar multipla).

    Ranking por sustentacao (confianca + probabilidade), nunca apenas
    pela maior frequencia historica.
    """
    pares: list[tuple[Candidato, Avaliacao]] = []
    for c in candidatos:
        for av in c.avaliacoes:
            pares.append((c, av))

    ranked = sorted(
        pares,
        key=lambda par: (par[1].confianca + par[1].prob, par[1].prob),
        reverse=True,
    )

    aprovadas: list[Avaliacao] = []
    fixtures_aprovados: set[int] = set()

    def _tenta(cand: Candidato, av: Avaliacao) -> bool:
        checks_cand = _auditar_candidato(client, cand)
        checks_av = _auditar_avaliacao(av, cand)
        all_checks = checks_cand + checks_av
        av.auditoria = all_checks
        cand.auditoria_candidato = checks_cand
        falha = next((nome for nome, ok, _ in all_checks if not ok), None)
        if falha:
            av.rejeicao = f"auditoria: {falha}"
            return False
        if av.prob < PROB_MIN_APROVAR:
            av.rejeicao = (
                f"probabilidade estimada {av.prob:.0%} < "
                f"{PROB_MIN_APROVAR:.0%}"
            )
            return False
        if av.prob > PROB_MAX_APROVAR:
            av.rejeicao = (
                f"linha praticamente decidida ({av.prob:.0%}): sem valor "
                "real de mercado nesta condicao"
            )
            return False
        if av.confianca < CONF_MIN_TOP1:
            av.rejeicao = f"confianca {av.confianca:.0%} < {CONF_MIN_TOP1:.0%}"
            return False
        if av.odd is not None and av.odd.atual:
            edge = av.edge
            if edge is not None and edge < EDGE_MINIMO:
                av.rejeicao = (
                    f"sem margem sobre a odd real: edge {edge:+.1%} "
                    f"(implicita {av.odd.implied:.0%})"
                )
                return False
        av.aprovada = True
        aprovadas.append(av)
        fixtures_aprovados.add(av.fixture_id)
        return True

    for cand, av in ranked:
        if len(aprovadas) >= 2:
            break
        if av.aprovada:
            continue
        # Familia RESULTADO: concorre em igualdade com gols/escanteios.
        # Sem odd ao vivo real a linha e aprovada como OPORTUNIDADE
        # ESTATISTICA (odd/bookmaker None); a convencao AH e exigida
        # pela auditoria da avaliacao quando aplicavel.
        if av.fixture_id in fixtures_aprovados:
            continue  # TOP 2 de jogo diferente (nunca multipla do mesmo jogo)
        _tenta(cand, av)

    # TOP 2 somente se tambem for forte
    if len(aprovadas) == 2 and aprovadas[1].confianca < CONF_MIN_TOP2:
        aprovadas[1].aprovada = False
        aprovadas[1].rejeicao = (
            f"TOP 2 exige sustentacao forte: confianca "
            f"{aprovadas[1].confianca:.0%} < {CONF_MIN_TOP2:.0%}"
        )
        aprovadas = aprovadas[:1]

    return aprovadas


def separar_aprovadas_por_cobertura(
    aprovadas: list[Avaliacao],
    cobertura: dict[int, list[Any]],
) -> tuple[list[Avaliacao], list[Avaliacao]]:
    """(permitidas, observacao) pela MATRIZ DE COBERTURA.

    Aprovada de mercado em OBSERVACAO (classe C: cobertura parcial)
    NUNCA vira recomendacao automatica: sai da lista de aprovadas e
    segue apenas para exibicao/calibracao. Nenhum calculo muda - so
    a elegibilidade (competicao x mercado x modo) decide.
    """
    permitidas: list[Avaliacao] = []
    observacao: list[Avaliacao] = []
    for av in aprovadas:
        veredictos = cobertura.get(av.fixture_id, [])
        if status_da_familia(veredictos, av.mercado) == STATUS_OBSERVACAO:
            av.aprovada = False
            av.rejeicao = (
                "cobertura classe C (observacao): mercado sem cobertura "
                "plena - nunca recomendacao automatica"
            )
            observacao.append(av)
        else:
            permitidas.append(av)
    return permitidas, observacao


# ----------------------------------------------------------------------
# Varredura completa
# ----------------------------------------------------------------------
def scan_live_opportunities(
    client: APIFootballClient,
    deep: int = MAX_DEEP,
    mercados: tuple[str, ...] | None = None,
) -> Varredura:
    """Responde 'Existe alguma oportunidade de aposta ao vivo agora?'.

    TRIAGEM GLOBAL (correcao validada pelo operador):

    1. TODOS os jogos ao vivo elegiveis passam por uma triagem barata
       (um snapshot por jogo, leitura cacheada TTL 60s - re-varreduras
       recentes nao gastam requisicoes);
    2. o score da triagem (_triagem_global_score) NUNCA ordena apenas
       pelo minuto mais avancado: um jogo aos 30'/45'/60' disputa de
       igual para igual com jogos mais adiantados;
    3. SOMENTE DEPOIS de todos os jogos terem sido avaliados
       superficialmente o limite de deep dive (`deep`) e aplicado -
       e o motivo informa o score da triagem, nunca "jogo menos
       maduro";
    4. apenas os `deep` escolhidos recebem refresh forcado do
       snapshot imediatamente antes do deep dive (o estado analisado
       e o mais fresco possivel, e o custo fica limitado a 2 jogos).
    """
    games = list_live_games(client)
    varredura = Varredura(
        hora=now_brt().strftime("%d/%m/%Y %H:%M:%S"),
        total_ao_vivo=len(games),
        mercados=mercados,
    )
    descartados: list[Descartado] = []

    # ---- Estagio 1: cortes baratos a partir da LISTAGEM (gratis) ----
    elegidos = []
    for g in games:
        jogo = f"{g.home_team_name} x {g.away_team_name}"
        motivo = _motivo_estagio1(g)
        if motivo:
            descartados.append(
                Descartado(jogo, g.fixture_id, g.elapsed, "todos", motivo)
            )
        else:
            elegidos.append(g)
    varredura.total_elegiveis = len(elegidos)
    # TODOS os elegiveis passam pela triagem barata (um snapshot cada)
    varredura.total_triados = len(elegidos)

    # ---- Estagio 2: TRIAGEM BARATA de TODOS os elegiveis ----
    # Um snapshot por jogo (cache-first, TTL de live): status/minuto,
    # placar, disponibilidade de estatisticas, quantidade de dados
    # live, competencia e presenca de historico em cache sao lidos
    # dali. NENHUM jogo elegivel fica de fora desta avaliacao.
    triados: list[LiveSnapshot] = []
    for g in elegidos:
        jogo = f"{g.home_team_name} x {g.away_team_name}"
        try:
            snap = fetch_live_snapshot(client, g.fixture_id)
        except IdentityDivergenceError:
            # REGRA 12: divergencia ID x nome INTERROMPE a varredura -
            # nunca continua a analise com outro clube por engano
            raise
        except UserFacingError as exc:
            descartados.append(Descartado(
                jogo, g.fixture_id, g.elapsed, "todos", str(exc)
            ))
            continue
        # MATRIZ DE COBERTURA (elegibilidade por mercado, ETAPA 10/11):
        # o antigo descarte generico "sem escanteios => jogo inteiro
        # fora" foi substituido pelo veredicto POR MERCADO. O jogo so
        # sai da triagem quando NENHUM mercado (GOLS/CORNERS/CARDS) tem
        # dados suficientes nesta leitura; um jogo sem Corner Kicks mas
        # com placar/estatisticas segue para analise de gols.
        veredictos = avaliar_cobertura_live(snap)
        if not familias_permitidas(veredictos):
            descartados.append(Descartado(
                jogo, g.fixture_id, snap.elapsed, "todos",
                "cobertura: nenhum mercado com dados suficientes "
                "nesta leitura (" + resumo_cobertura(veredictos) + ")",
            ))
            continue
        varredura.cobertura[snap.fixture_id] = veredictos
        triados.append(snap)

    # ---- Ranking da triagem global: TODOS ja avaliados aqui ----
    pontuados = [(_triagem_global_score(s, client), s) for s in triados]
    pontuados.sort(key=lambda par: (par[0], par[1].elapsed or 0),
                   reverse=True)
    varredura.sondados = [
        {
            "jogo": f"{s.home_team_name} x {s.away_team_name}",
            "fixture_id": s.fixture_id,
            "competicao": s.league_name or "",
            "minuto": s.elapsed,
            "status": s.status,
            "placar": f"{s.goals_home}-{s.goals_away}",
            "score_triagem": score,
        }
        for score, s in pontuados
    ]

    # ---- Estagio 3: limite de deep dive aplicado SOMENTE AQUI ----
    # (depois de todos os jogos terem sido avaliados superficialmente)
    profundos = [s for _score, s in pontuados[:deep]]
    for score, s in pontuados[deep:]:
        descartados.append(Descartado(
            f"{s.home_team_name} x {s.away_team_name}",
            s.fixture_id, s.elapsed, "todos",
            f"triagem global: avaliado superficialmente (score {score:.1f}"
            f" de 11); nao esta entre os {deep} melhores da varredura "
            f"(limite de requisicoes do deep dive, nao de minuto)",
        ))

    varredura.descartados = descartados

    # ---- Estagio 4: deep dive dos escolhidos (snapshot fresco) ----
    for snap in profundos:
        fresco = fetch_live_snapshot(client, snap.fixture_id, refresh=True)
        # MATRIZ DE COBERTURA revalidada na leitura FRESCA: o veredicto
        # da triagem usou o snapshot cacheado; o deep dive so avalia as
        # familias cujo dado essencial existe AGORA (ETAPA 10). Isso
        # tambem economiza o processamento caro: mercado sem dado =>
        # familia nem entra no deep dive (ETAPA 11).
        veredictos = avaliar_cobertura_live(fresco)
        varredura.cobertura[fresco.fixture_id] = veredictos
        familias = familias_para_deep_dive(veredictos, mercados)
        if familias == ():  # lista VAZIA => nenhum mercado liberado
            descartados.append(Descartado(
                f"{fresco.home_team_name} x {fresco.away_team_name}",
                fresco.fixture_id, fresco.elapsed, "todos",
                "cobertura: dados insuficientes apos o refresh ("
                + resumo_cobertura(veredictos) + ")",
            ))
            continue
        cand = deep_dive(client, fresco, mercados=familias)
        cand.cobertura = veredictos
        varredura.candidatos.append(cand)
    varredura.descartados = descartados

    varredura.aprovadas = _aprovar(client, varredura.candidatos)
    # MATRIZ DE COBERTURA: aprovada de mercado em OBSERVACAO (classe C)
    # NUNCA vira recomendacao automatica - sai da lista de aprovadas e
    # segue apenas para exibicao/calibracao. Nenhum calculo muda.
    (varredura.aprovadas,
     varredura.observacao) = separar_aprovadas_por_cobertura(
        varredura.aprovadas, varredura.cobertura)
    return varredura


# ----------------------------------------------------------------------
# Analise de um jogo especifico ("Analise este jogo ao vivo.")
# ----------------------------------------------------------------------
def analyze_live_game_opportunities(
    client: APIFootballClient, spec: str
) -> Candidato:
    """Deep dive completo de UM jogo ao vivo (escanteios, gols, cartoes
    com linha real, AH/DNB/DC com linha real). Sem forcar aprovacao.

    MATRIZ DE COBERTURA: so entram no deep dive as familias cujo dado
    essencial existe nesta leitura (veredictos anexados ao candidato
    para o relatorio de elegibilidade)."""
    fixture_id = find_live_fixture_id(client, spec)
    snap = fetch_live_snapshot(client, fixture_id, refresh=True)
    veredictos = avaliar_cobertura_live(snap)
    familias = familias_para_deep_dive(veredictos)
    if familias == ():  # nenhum mercado com dados suficientes
        cand = Candidato(snapshot=snap)
        cand.cobertura = veredictos
        return cand
    cand = deep_dive(client, snap, mercados=familias)
    cand.cobertura = veredictos
    return cand