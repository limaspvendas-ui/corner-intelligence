"""ETAPA 5 -- ENGINE DE BACKTEST DEDICADO DO CORNER INTELLIGENCE.

Mecanismo de backtest SEPARADO do fluxo operacional, REPRODUTIVEL, com
trava anti-lookahead/anti-vazamento (AS_OF / MOMENTO DA DECISAO). Nao
reutiliza o ledger operacional (data/recomendacoes.db); grava em
data/backtest.db proprio. Nenhum parametro do motor e otimizado -- as
regras sao avaliadas COMO ESTAO (FASE G: regras congeladas).

Principios (REGRA CENTRAL: PRECISAO > QUANTIDADE):
  - FATO -> CALCULO -> INTERPRETACAO -> DECISAO.
  - Nunca inventar dado; nunca reconstruir historico que nao existe;
    nunca usar resultado futuro no calculo da previsao; nunca
    transformar missing em zero; nunca calcular ROI sem odd real.
  - Todo dado utilizado possui data < AS_OF (momento da decisao).

Tres modos (FASE A):
  - PRE        : backtestavel (4906 fixtures com stats + 1H + resultado
                 final no cache). Historico + benchmarks reconstruidos
                 do cache com filtro AS_OF. Motor invocado diretamente
                 (avaliar_pregame / avaliar_resultado_prejogo /
                 avaliar_cartoes_prejogo + aprovar_pregame + matriz de
                 cobertura da Etapa 4).
  - LIVE_HT    : parcialmente backtestavel. O snapshot do INTERVALO
                 (minuto ~45, status HT) e HONESTAMENTE reconstruido das
                 estatisticas_1h + placar do intervalo. deep_dive e
                 reusado INTEGRALMENTE via CacheClient (proxy AS_OF sobre
                 o cache). So existe no minuto 45 -- nao minutos
                 arbitrarios, nao fotografia final como trajetoria.
  - PRESSAO    : AINDA NAO AVALIAVEL (tabela live_snapshot_history nao
                 existe; 1 linha em live_snapshots). Nenhum snapshot
                 temporal historico. Declarado sem taxa de acerto.

Assentamento (FASE L): reusa as funcoes nucleares de src/settlement.py
(_parse_linha, _resultado_do_total, _liquidar_resultado) e
src/handicap.py (settle) contra o resultado final conhecido do cache.
Nenhum cliente de rede -- os totais finais vem das estatisticas finais
cacheadas. Missing => NAO AVALIAVEL, nunca zero.

Exemplo:
    python -m src.backtest run --modo PRE --saida data/backtest.db
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sqlite3
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any

from src.cobertura import (
    BACKTEST_A_CONFIRMAR,
    BACKTEST_INVIÁVEL,
    BACKTEST_PARCIAL,
    BACKTEST_VIAVEL,
    MERCADO_CARDS,
    MERCADO_CORNERS,
    MERCADO_DA_FAMILIA,
    MERCADO_GOALS,
    MERCADO_RESULTADO,
    MODO_LIVE,
    MODO_PRE_GAME,
    STATUS_PRESSAO_EXPERIMENTAL,
    STATUS_RESULTADO_EXPERIMENTAL,
    avaliar_cobertura_pre,
    filtrar_avaliacoes_por_cobertura,
    status_backtest,
    status_resultado,
)
from src.config import DEFAULT_LAST_N, DATA_DIR, DB_PATH
from src.exceptions import DataUnavailableError, UserFacingError
from src.fixtures import FINISHED_STATUS, Fixture, parse_fixture
from src.match_stats import (
    MatchStats,
    SideStats,
    _parse_side,
    to_team_perspective,
)
from src.prejogo_opportunity import (
    VERSAO_PREJOGO_OP,
    aprovar_pregame,
    avaliar_pregame,
)
from src.live_opportunity import (
    CONF_MIN_TOP1,
    PROB_MAX_APROVAR,
    PROB_MIN_APROVAR,
)
from src.resultado import avaliar_resultado_prejogo
from src.cartoes import avaliar_cartoes_prejogo
from src.settlement import (
    NAO_AVALIAVEL,
    _MARCA_CONVENCAO_CARTOES,
    _e_linha_resultado,
    _liquidar_resultado,
    _parse_linha,
    _resultado_do_total,
)
from src.stats import describe

# Versao do engine de backtest (NAO confundir com versao das regras do
# motor, que e congelada e lida das fontes originais).
VERSAO_BACKTEST_ENGINE = "backtest-1.0"

# Maturidade de amostra (FASE I) -- NENHUM auto-promove para "validado".
MIN_AMOSTRA_OBSERVACAO = 10      # abaixo: AMOSTRA INSUFICIENTE
MIN_AMOSTRA_MADURA = 30          # acima: AMOSTRA MADURA
# Nenhum desses limites promove uma regra a "validada": apenas rotula a
# maturidade da amostra. Promocao exige validacao estatistica formal,
# fora do escopo deste backtest.

# Armazenamento SEPARADO do ledger operacional (FASE E).
BACKTEST_DB_PATH = str(DATA_DIR / "backtest.db")

# Status de decisao simulada (FASE H)
DEC_ENTRAR = "ENTRAR"
DEC_AGUARDAR = "AGUARDAR"
DEC_DESCARTAR = "DESCARTAR"
DEC_DESCARTAR_COBERTURA = "DESCARTAR (COBERTURA)"
DEC_NAO_AVALIAVEL = "NÃO AVALIÁVEL"

# Status de liquidacao (espelha src/settlement.py)
RES_GANHA = "GANHA"
RES_PERDIDA = "PERDIDA"
RES_DEVOLVIDA = "DEVOLVIDA"
RES_MEIA_VIT = "MEIA VITÓRIA"
RES_MEIA_DER = "MEIA DERROTA"


# ----------------------------------------------------------------------
# Utilidades de data (anti-lookahead)
# ----------------------------------------------------------------------
def _dt(d: str | None) -> datetime | None:
    """Parse robusto de data ISO (com offset) para comparacao. None
    permanece None -- nunca vira epoca zero."""
    if not d:
        return None
    try:
        return datetime.fromisoformat(d)
    except (TypeError, ValueError):
        # Fallback: pega so o prefixo ISO (YYYY-MM-DDTHH:MM:SS)
        try:
            return datetime.strptime(d[:19], "%Y-%m-%dT%H:%M:%S")
        except (TypeError, ValueError, IndexError):
            return None


def _antes(a: str | None, b: str | None, estrito: bool = True) -> bool:
    """True se a < b (ou <= se estrito=False). Ausencia => False
    (nunca assume ordenamento de dado desconhecido)."""
    da, db = _dt(a), _dt(b)
    if da is None or db is None:
        return False
    return da < db if estrito else da <= db


# ----------------------------------------------------------------------
# Indice do cache (le o cache da API; SEM rede)
# ----------------------------------------------------------------------
class CacheIndex:
    """Carrega o cache SQLite (api_cache) em memoria e prove acesso
    FILTRADO POR AS_OF (anti-lookahead). Nao faz requisicoes.

    Guarda:
      - fixtures crus (dict da API) por fixture_id (para o CacheClient
        que serve o deep_dive live);
      - Fixture parseado por id;
      - blocos de estatistica por fixture (full / 1h / 2h);
      - times por id (para validacao de identidade do path live);
      - odds pre-jogo por fixture (para o subset com odd real -> ROI).
    """

    def __init__(self, db_path: str = DB_PATH) -> None:
        self.db_path = str(db_path)
        self._raw_fixtures: dict[int, dict[str, Any]] = {}
        self._fixtures: dict[int, Fixture] = {}
        self._stat_blocks: dict[int, list[dict[str, Any]]] = {}
        self._has_1h: set[int] = set()
        self._teams_by_id: dict[int, dict[str, Any]] = {}
        self._odds: dict[int, list[dict[str, Any]]] = {}
        self._loaded = False

    def load(self) -> None:
        with sqlite3.connect(self.db_path) as conn:
            self._load_stats(conn)
            self._load_fixtures(conn)
            self._load_teams(conn)
            self._load_odds(conn)
        self._loaded = True

    # -- carregadores --
    def _load_stats(self, conn: sqlite3.Connection) -> None:
        rows = conn.execute(
            "SELECT params, response FROM api_cache "
            "WHERE endpoint = '/fixtures/statistics'"
        ).fetchall()
        # Priorizar a variante half=true (tem 1h/2h); fallback no-half.
        por_fx: dict[int, list[dict[str, Any]]] = {}
        por_fx_half: dict[int, list[dict[str, Any]]] = {}
        for params, resp in rows:
            try:
                p = json.loads(params)
                fx = int(p.get("fixture"))
            except (TypeError, ValueError, KeyError):
                continue
            try:
                blocos = json.loads(resp)
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
            if not isinstance(blocos, list):
                continue
            is_half = p.get("half") == "true"
            if is_half:
                por_fx_half[fx] = blocos
            else:
                por_fx.setdefault(fx, blocos)
        # Merge: half=true ganha; no-half so entra se nao houver half.
        for fx, blocos in por_fx_half.items():
            self._stat_blocks[fx] = blocos
            if any("statistics_1h" in b for b in blocos):
                self._has_1h.add(fx)
        for fx, blocos in por_fx.items():
            if fx not in self._stat_blocks:
                self._stat_blocks[fx] = blocos

    def _load_fixtures(self, conn: sqlite3.Connection) -> None:
        rows = conn.execute(
            "SELECT params, response FROM api_cache WHERE endpoint = '/fixtures'"
        ).fetchall()
        for params, resp in rows:
            try:
                lista = json.loads(resp)
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
            if not isinstance(lista, list):
                continue
            for raw in lista:
                if not isinstance(raw, dict):
                    continue
                fid = (raw.get("fixture") or {}).get("id")
                if fid is None:
                    continue
                fid = int(fid)
                # Dedup: guarda o primeiro; todos sao a mesma partida.
                if fid not in self._raw_fixtures:
                    self._raw_fixtures[fid] = raw
                    try:
                        self._fixtures[fid] = parse_fixture(raw)
                    except Exception:
                        # fixture malformado no cache -> ignorado
                        pass

    def _load_teams(self, conn: sqlite3.Connection) -> None:
        rows = conn.execute(
            "SELECT params, response FROM api_cache WHERE endpoint = '/teams'"
        ).fetchall()
        for params, resp in rows:
            try:
                lista = json.loads(resp)
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
            if not isinstance(lista, list) or not lista:
                continue
            raw = lista[0]
            tid = (raw.get("team") or {}).get("id")
            if tid is None:
                continue
            self._teams_by_id[int(tid)] = raw

    def _load_odds(self, conn: sqlite3.Connection) -> None:
        rows = conn.execute(
            "SELECT params, response FROM api_cache WHERE endpoint = '/odds'"
        ).fetchall()
        for params, resp in rows:
            try:
                p = json.loads(params)
                fx = int(p.get("fixture"))
            except (TypeError, ValueError, KeyError):
                continue
            try:
                lista = json.loads(resp)
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
            if isinstance(lista, list) and lista:
                self._odds[fx] = lista

    # -- acessos basicos --
    @property
    def n_fixtures(self) -> int:
        return len(self._fixtures)

    @property
    def n_with_stats(self) -> int:
        return len(self._stat_blocks)

    @property
    def n_with_1h(self) -> int:
        return len(self._has_1h)

    @property
    def n_with_odds(self) -> int:
        return len(self._odds)

    def fixture(self, fid: int) -> Fixture | None:
        return self._fixtures.get(fid)

    def raw_fixture(self, fid: int) -> dict[str, Any] | None:
        return self._raw_fixtures.get(fid)

    def has_stats(self, fid: int) -> bool:
        return fid in self._stat_blocks

    def has_1h(self, fid: int) -> bool:
        return fid in self._has_1h

    def odds_for(self, fid: int) -> list[dict[str, Any]] | None:
        return self._odds.get(fid)

    def stat_blocks(self, fid: int) -> list[dict[str, Any]] | None:
        return self._stat_blocks.get(fid)

    def match_stats(self, fixture: Fixture) -> MatchStats | None:
        """MatchStats do jogo COMPLETO a partir do cache. None se nao
        houver estatistica cacheada para a partida."""
        blocos = self._stat_blocks.get(fixture.fixture_id)
        if not blocos:
            return None
        by_team: dict[int, SideStats] = {}
        by_team_1h: dict[int, SideStats] = {}
        by_team_2h: dict[int, SideStats] = {}
        for b in blocos:
            tid = (b.get("team") or {}).get("id")
            if tid is None:
                continue
            by_team[int(tid)] = _parse_side(b.get("statistics"))
            if b.get("statistics_1h"):
                by_team_1h[int(tid)] = _parse_side(b["statistics_1h"])
            if b.get("statistics_2h"):
                by_team_2h[int(tid)] = _parse_side(b["statistics_2h"])
        home = by_team.get(fixture.home_team_id, SideStats())
        away = by_team.get(fixture.away_team_id, SideStats())
        return MatchStats(
            fixture=fixture,
            home=home,
            away=away,
            first_home=by_team_1h.get(fixture.home_team_id),
            first_away=by_team_1h.get(fixture.away_team_id),
            second_home=by_team_2h.get(fixture.home_team_id),
            second_away=by_team_2h.get(fixture.away_team_id),
        )

    def finished_fixtures(
        self, league_id: int | None = None, season: str | None = None,
    ) -> list[Fixture]:
        """Fixtures encerrados no cache, opcionalmente filtrados por
        liga+temporada. NAO filtra por AS_OF aqui (o caller decide)."""
        out = []
        for fx in self._fixtures.values():
            if not fx.is_finished:
                continue
            if league_id is not None and fx.league_id != league_id:
                continue
            if season is not None and str(fx.season) != str(season):
                continue
            out.append(fx)
        out.sort(key=lambda f: f.date or "")
        return out

    # -- acessos AS_OF (anti-lookahead) --
    def team_history(
        self, team_id: int, as_of: str | None, last: int = DEFAULT_LAST_N,
    ) -> tuple[list[Any], int]:
        """Replica fetch_team_history com filtro AS_OF: ultimos `last`
        jogos ENCERRADOS do time com data < as_of, com estatistica
        cacheada. Retorna (jogos_validos, sem_estatisticas).

        Anti-lookahead: a partida alvo (data == as_of) e qualquer jogo
        posterior sao EXCLUIDOS do historico. Jogos sem estatistica no
        cache sao contabilizados (nunca completados artificialmente)."""
        candidatos = [
            f for f in self._fixtures.values()
            if f.is_finished and (
                f.home_team_id == team_id or f.away_team_id == team_id
            )
        ]
        # Filtro AS_OF: data estritamente anterior ao momento da decisao
        candidatos = [
            f for f in candidatos if _antes(f.date, as_of, estrito=True)
        ]
        candidatos.sort(key=lambda f: f.date or "", reverse=True)
        candidatos = candidatos[:last]

        games: list[Any] = []
        unavailable = 0
        for fx in candidatos:
            ms = self.match_stats(fx)
            if ms is None:
                unavailable += 1
                continue
            try:
                games.append(to_team_perspective(ms, team_id))
            except DataUnavailableError:
                # escanteios incompletos -> conta como sem estatistica
                unavailable += 1
        return games, unavailable

    def _league_finished_before(
        self, league_id: int, season: str | None, as_of: str | None,
    ) -> list[Fixture]:
        return [
            f for f in self.finished_fixtures(league_id, season)
            if _antes(f.date, as_of, estrito=True)
        ]

    def league_benchmark_corners(
        self, league_id: int, season: str | None, as_of: str | None,
        league_name: str | None = None,
    ) -> dict[str, Any] | None:
        """Media de escanteios da liga sobre a competicao INTEIRA,
        restrita a partidas ENCERRADAS com data < as_of (anti-lookahead).
        Replica league_corner_average; partida sem escanteios e excluida
        e contabilizada -- nunca zerada."""
        jogos = self._league_finished_before(league_id, season, as_of)
        totais: list[float] = []
        sem = 0
        for fx in jogos:
            ms = self.match_stats(fx)
            if ms is None or ms.home.corners is None or ms.away.corners is None:
                sem += 1
                continue
            totais.append(float(ms.home.corners + ms.away.corners))
        if not totais:
            return None
        return {
            "liga": league_name or f"Liga {league_id}",
            "partidas_validas": len(totais),
            "partidas_sem_escanteios": sem,
            "describe": describe(totais),
        }

    def league_benchmark_goals(
        self, league_id: int, season: str | None, as_of: str | None,
        league_name: str | None = None,
    ) -> dict[str, Any] | None:
        """Media de gols da liga (placar) sobre encerradas com data <
        as_of. Replica _benchmark_gols_liga."""
        jogos = self._league_finished_before(league_id, season, as_of)
        totais: list[float] = []
        for fx in jogos:
            if fx.goals_home is None or fx.goals_away is None:
                continue
            totais.append(float(fx.goals_home + fx.goals_away))
        if not totais:
            return None
        return {
            "liga": league_name or f"Liga {league_id}",
            "partidas_validas": len(totais),
            "describe": describe(totais),
        }

    def league_benchmark_cards(
        self, league_id: int, season: str | None, as_of: str | None,
        league_name: str | None = None,
    ) -> dict[str, Any] | None:
        """Media de PONTOS de cartoes (amarelo=1, vermelho=2) da liga
        sobre encerradas com data < as_of com cartoes completos."""
        jogos = self._league_finished_before(league_id, season, as_of)
        totais: list[float] = []
        for fx in jogos:
            ms = self.match_stats(fx)
            if ms is None:
                continue
            vals = (ms.home.yellow_cards, ms.away.yellow_cards,
                    ms.home.red_cards, ms.away.red_cards)
            if any(v is None for v in vals):
                continue
            totais.append(float(
                ms.home.yellow_cards + ms.away.yellow_cards
                + 2 * (ms.home.red_cards + ms.away.red_cards)
            ))
        if not totais:
            return None
        return {
            "liga": league_name or f"Liga {league_id}",
            "partidas_validas": len(totais),
            "describe": describe(totais),
        }

    def h2h_n(
        self, team_a: int, team_b: int, as_of: str | None,
    ) -> int:
        """Qty de confrontos diretos ENCERRADOS (entre os dois times) com
        data < as_of e com estatistica cacheada. Replica
        H2HResult.with_stats com filtro AS_OF."""
        n = 0
        for fx in self._fixtures.values():
            if not fx.is_finished:
                continue
            if not fx.involves(team_a, team_b):
                continue
            if not _antes(fx.date, as_of, estrito=True):
                continue
            if self.match_stats(fx) is not None:
                n += 1
        return n


# ----------------------------------------------------------------------
# CacheClient: proxy AS_OF sobre o cache para reusar deep_dive (LIVE_HT)
# ----------------------------------------------------------------------
class CacheClient:
    """Adapter que simula APIFootballClient.get sobre o CacheIndex,
    filtrando por AS_OF. Permite reusar deep_dive INTEGRALMENTE no
    backtest LIVE_HT sem rede e sem vazamento futuro.

    Endpoints servidos:
      /fixtures            (team+last | team+league+season | league+season
                            | id | headtohead) -- sempre data < AS_OF
      /fixtures/statistics (fixture [+half]) -- do cache
      /teams               (id) -- do cache (ausente => [])
      /odds                (fixture) -- do cache (pre-jogo)
      /odds/live           -- sempre [] (auditoria: indisponivel na fonte)
    """

    def __init__(self, index: CacheIndex, as_of: str | None) -> None:
        self._idx = index
        self._as_of = as_of

    def get(self, endpoint: str, params: dict[str, Any] | None = None,
            ) -> list[dict[str, Any]] | None:
        params = params or {}
        try:
            if endpoint == "/fixtures/statistics":
                fx = params.get("fixture")
                if fx is None:
                    return []
                blocos = self._idx.stat_blocks(int(fx))
                return blocos if blocos is not None else []
            if endpoint == "/teams":
                tid = params.get("id")
                if tid is None:
                    return []
                raw = self._idx._teams_by_id.get(int(tid))
                return [raw] if raw else []
            if endpoint == "/odds":
                fx = params.get("fixture")
                if fx is None:
                    return []
                odds = self._idx.odds_for(int(fx))
                return odds if odds is not None else []
            if endpoint == "/odds/live":
                # Auditoria Etapa 4: odds live indisponiveis na fonte.
                return []
            if endpoint == "/fixtures/headtohead":
                h2h = params.get("h2h", "")
                try:
                    a, b = (int(x) for x in str(h2h).split("-"))
                except ValueError:
                    return []
                last = int(params.get("last", 10))
                rows = [
                    self._idx.raw_fixture(f.fixture_id)
                    for f in self._idx._fixtures.values()
                    if f.is_finished and f.involves(a, b)
                    and _antes(f.date, self._as_of, estrito=True)
                ]
                rows = [r for r in rows if r is not None]
                rows.sort(
                    key=lambda r: (r.get("fixture") or {}).get("date") or "",
                    reverse=True,
                )
                return rows[:last]
            if endpoint == "/fixtures":
                return self._fixtures(params)
        except Exception:
            return []
        return []

    def _fixtures(self, params: dict[str, Any]) -> list[dict[str, Any]]:
        # Por id
        if "id" in params:
            fx = self._idx.fixture(int(params["id"]))
            if fx is None or not _antes(fx.date, self._as_of, estrito=False):
                return []
            raw = self._idx.raw_fixture(fx.fixture_id)
            return [raw] if raw else []
        # Por team + league + season (validacao de identidade)
        if "team" in params and "league" in params and "season" in params:
            tid = int(params["team"])
            lid = int(params["league"])
            season = str(params["season"])
            rows = []
            for f in self._idx._fixtures.values():
                if f.home_team_id != tid and f.away_team_id != tid:
                    continue
                if f.league_id != lid or str(f.season) != season:
                    continue
                if not _antes(f.date, self._as_of, estrito=False):
                    continue
                raw = self._idx.raw_fixture(f.fixture_id)
                if raw is not None:
                    rows.append(raw)
            return rows
        # Por team + last (historico)
        if "team" in params:
            tid = int(params["team"])
            last = int(params.get("last", 20))
            rows = [
                (f, self._idx.raw_fixture(f.fixture_id))
                for f in self._idx._fixtures.values()
                if f.is_finished and (
                    f.home_team_id == tid or f.away_team_id == tid
                ) and _antes(f.date, self._as_of, estrito=True)
            ]
            rows.sort(key=lambda r: r[0].date or "", reverse=True)
            return [r[1] for r in rows[:last] if r[1] is not None]
        # Por league + season (benchmark)
        if "league" in params and "season" in params:
            lid = int(params["league"])
            season = str(params["season"])
            rows = []
            for f in self._idx._fixtures.values():
                if f.league_id != lid or str(f.season) != season:
                    continue
                if not _antes(f.date, self._as_of, estrito=True):
                    continue
                raw = self._idx.raw_fixture(f.fixture_id)
                if raw is not None:
                    rows.append(raw)
            return rows
        return []


# ----------------------------------------------------------------------
# Odds reais (FASE H: ROI somente onde houver odd real)
# ----------------------------------------------------------------------
_ODDS_BET_BY_UNIT = {
    "escanteios": "Corners Over Under",
    "gols": "Goals Over/Under",
}
_ODDS_BET_1X2 = "Match Winner"
_ODDS_BET_DC = "Double Chance"
_ODDS_BET_AH = "Asian Handicap"

# Mapeamento linha-resultado -> valor de odd
_MAP_1X2 = {
    "Vitoria mandante (1)": "Home",
    "Empate (X)": "Draw",
    "Vitoria visitante (2)": "Away",
}
_MAP_DC = {
    "Dupla chance 1X": "Home/Draw",
    "Dupla chance 12": "Home/Away",
    "Dupla chance X2": "Draw/Away",
}


def extrair_odd_real(
    odds_response: list[dict[str, Any]] | None, linha: str, mercado: str,
) -> tuple[float | None, str | None]:
    """(odd, bookmaker) da melhor price para a linha, ou (None, None).

    Procura em TODOS os bookmakers da resposta e fica com a MAIOR odd
    (best price realista para o apostador). Nunca inventa odd: se a
    linha nao casa com nenhum valor oferecido, retorna (None, None)."""
    if not odds_response or not linha:
        return None, None
    best: float | None = None
    best_bm: str | None = None
    for entry in odds_response:
        for bm in entry.get("bookmakers") or []:
            bm_name = bm.get("name")
            for bet in bm.get("bets") or []:
                alvo = _valor_odd_alvo(linha, mercado, bet.get("name") or "")
                if alvo is None:
                    continue
                for v in bet.get("values") or []:
                    if (v.get("value") or "").strip() == alvo:
                        try:
                            odd = float(v.get("odd"))
                        except (TypeError, ValueError):
                            continue
                        if best is None or odd > best:
                            best = odd
                            best_bm = bm_name
    return best, best_bm


def _valor_odd_alvo(linha: str, mercado: str, bet_name: str) -> str | None:
    """String do valor de odd a procurar para a linha/mercado, ou None
    se o mercado nao tem odds na fonte ou a linha nao casa."""
    txt = (linha or "").strip()
    # Totais over/under
    parsed = _parse_linha(txt)
    if parsed is not None:
        direcao, valor, unidade = parsed
        bet_esperado = _ODDS_BET_BY_UNIT.get(unidade)
        if bet_esperado is None or bet_name != bet_esperado:
            return None
        # valor formatado igual ao da fonte ("Over 2.5", "Under 9.5")
        vstr = f"{valor:g}"
        return f"{direcao} {vstr}"
    # 1X2
    if txt in _MAP_1X2 and bet_name == _ODDS_BET_1X2:
        return _MAP_1X2[txt]
    # Dupla chance
    if txt in _MAP_DC and bet_name == _ODDS_BET_DC:
        return _MAP_DC[txt]
    # AH: "AH mandante +0.5 (90 minutos)" -> "Home +0.5"
    if txt.startswith("AH ") and bet_name == _ODDS_BET_AH:
        # _RE_AH: AH (mandante|visitante) ([+-]?\d+(?:[.,]\d+)?) (90 minutos)
        try:
            resto = txt[3:]
            lado, resto2 = resto.split(" ", 1)
            valor_ah = resto2.split(" (90")[0].strip().replace(",", ".")
            lado_odd = "Home" if lado == "mandante" else "Away"
            return f"{lado_odd} {valor_ah}"
        except Exception:
            return None
    return None


# ----------------------------------------------------------------------
# Assentamento (FASE L: reusa settlement.py)
# ----------------------------------------------------------------------
def liquidar(
    linha: str, mercado: str, fixture: Fixture,
    match: MatchStats | None,
) -> tuple[str | None, str]:
    """(resultado, nota) pela regra real de settlement, contra o
    resultado final conhecido do cache. None/NAO_AVALIAVEL quando o
    dado final necessario esta ausente -- nunca zero, nunca inferido."""
    txt = (linha or "").strip()
    # Familia RESULTADO: liquida exclusivamente pelo placar final.
    if _e_linha_resultado(txt):
        if fixture.goals_home is None or fixture.goals_away is None:
            return NAO_AVALIAVEL, "placar final nao disponivel; nunca inferido"
        return _liquidar_resultado(txt, int(fixture.goals_home),
                                   int(fixture.goals_away))
    # Totais over/under
    parsed = _parse_linha(txt)
    if parsed is None:
        return NAO_AVALIAVEL, "mercado sem regra de liquidacao automatica"
    direcao, valor, unidade = parsed
    if unidade == "gols":
        if fixture.goals_home is None or fixture.goals_away is None:
            return NAO_AVALIAVEL, "placar final nao disponivel; nunca inferido"
        total = float(fixture.goals_home + fixture.goals_away)
        return _resultado_do_total(direcao, total, valor), (
            f"total gols {total:g} x {direcao} {valor:g}"
        )
    if unidade == "escanteios":
        if match is None or match.home.corners is None or match.away.corners is None:
            return NAO_AVALIAVEL, "escanteios finais nao disponiveis; nunca inferido"
        total = float(match.home.corners + match.away.corners)
        return _resultado_do_total(direcao, total, valor), (
            f"total escanteios {total:g} x {direcao} {valor:g}"
        )
    if unidade == "cartoes":
        if _MARCA_CONVENCAO_CARTOES not in txt:
            return NAO_AVALIAVEL, (
                "cartoes sem convencao declarada na linha; pesos nunca assumidos"
            )
        if match is None or any(v is None for v in (
            match.home.yellow_cards, match.away.yellow_cards,
            match.home.red_cards, match.away.red_cards,
        )):
            return NAO_AVALIAVEL, "cartoes finais nao disponiveis; nunca inferido"
        total = float(
            match.home.yellow_cards + match.away.yellow_cards
            + 2 * (match.home.red_cards + match.away.red_cards)
        )
        return _resultado_do_total(direcao, total, valor), (
            f"total cartoes (pontos) {total:g} x {direcao} {valor:g}"
        )
    return NAO_AVALIAVEL, "mercado sem regra de liquidacao automatica"


def _ganho_unitario(resultado: str, odd: float | None) -> float | None:
    """P/L por stake unitario (1u) para o resultado. None quando nao se
    pode calcular (sem odd real OU resultado NAO AVALIAVEL/DEVOLVIDA sem
    odd). Devolucao = 0 (stake retornado)."""
    if odd is None:
        # Sem odd real: nenhum P/L financeiro e calculado (FASE H).
        if resultado in (RES_GANHA, RES_PERDIDA, RES_MEIA_VIT, RES_MEIA_DER):
            return None
        if resultado == RES_DEVOLVIDA:
            return 0.0
        return None
    if resultado == RES_GANHA:
        return odd - 1.0
    if resultado == RES_MEIA_VIT:
        return (odd - 1.0) / 2.0
    if resultado == RES_MEIA_DER:
        return -0.5
    if resultado == RES_PERDIDA:
        return -1.0
    if resultado == RES_DEVOLVIDA:
        return 0.0
    return None


# ----------------------------------------------------------------------
# Registro de uma previsao simulada
# ----------------------------------------------------------------------
@dataclass
class BacktestPrediction:
    run_id: str
    fixture_id: int
    league_id: int | None
    league_name: str | None
    season: str | None
    home_team: str | None
    away_team: str | None
    date: str | None
    as_of: str | None          # momento da decisao (anti-lookahead)
    modo: str                  # PRE / LIVE_HT
    mercado: str               # escanteios / gols / cartoes / resultado
    linha: str
    prob: float
    confianca: float
    h2h_n: int
    n_home: int
    n_away: int
    sem_stats_home: int
    sem_stats_away: int
    bench_validas: int | None
    decisao: str               # ENTRAR / AGUARDAR / DESCARTAR / ...
    cobertura_mercado: str | None
    experimental: bool         # RESULTADO/PRESSAO experimental
    odd_real: float | None
    bookmaker: str | None
    predicted_edge: float | None
    predicted_ev: float | None
    resultado_final: str | None
    placar_final: str | None
    total_final: float | None
    pl_unitario: float | None


@dataclass
class BacktestRunMeta:
    run_id: str
    engine_versao: str
    regras_versao: str
    modo: str
    datahora: str
    intervalo_inicio: str | None
    intervalo_fim: str | None
    ligas: str                 # CSV de IDs
    mercados: str              # CSV
    fixtures_considerados: int
    fixtures_excluidos: int
    previsoes: int
    nota: str


# ----------------------------------------------------------------------
# Armazenamento SEPARADO (data/backtest.db)
# ----------------------------------------------------------------------
class BacktestStore:
    """Banco proprio do backtest. NUNCA toca data/recomendacoes.db."""

    def __init__(self, db_path: str = BACKTEST_DB_PATH) -> None:
        self.db_path = str(db_path)
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self._init()

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.db_path)

    def _init(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """CREATE TABLE IF NOT EXISTS bt_runs (
                    run_id TEXT PRIMARY KEY,
                    engine_versao TEXT NOT NULL,
                    regras_versao TEXT NOT NULL,
                    modo TEXT NOT NULL,
                    datahora TEXT NOT NULL,
                    intervalo_inicio TEXT,
                    intervalo_fim TEXT,
                    ligas TEXT,
                    mercados TEXT,
                    fixtures_considerados INTEGER,
                    fixtures_excluidos INTEGER,
                    previsoes INTEGER,
                    nota TEXT
                )"""
            )
            conn.execute(
                """CREATE TABLE IF NOT EXISTS bt_predictions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL,
                    fixture_id INTEGER,
                    league_id INTEGER,
                    league_name TEXT,
                    season TEXT,
                    home_team TEXT,
                    away_team TEXT,
                    date TEXT,
                    as_of TEXT,
                    modo TEXT,
                    mercado TEXT,
                    linha TEXT,
                    prob REAL,
                    confianca REAL,
                    h2h_n INTEGER,
                    n_home INTEGER,
                    n_away INTEGER,
                    sem_stats_home INTEGER,
                    sem_stats_away INTEGER,
                    bench_validas INTEGER,
                    decisao TEXT,
                    cobertura_mercado TEXT,
                    experimental INTEGER,
                    odd_real REAL,
                    bookmaker TEXT,
                    predicted_edge REAL,
                    predicted_ev REAL,
                    resultado_final TEXT,
                    placar_final TEXT,
                    total_final REAL,
                    pl_unitario REAL
                )"""
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_bt_pred_run "
                "ON bt_predictions(run_id)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_bt_pred_fx "
                "ON bt_predictions(fixture_id)"
            )

    def salvar_run(self, meta: BacktestRunMeta,
                   previsoes: list[BacktestPrediction]) -> None:
        with self._connect() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO bt_runs
                   (run_id, engine_versao, regras_versao, modo, datahora,
                    intervalo_inicio, intervalo_fim, ligas, mercados,
                    fixtures_considerados, fixtures_excluidos, previsoes, nota)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (meta.run_id, meta.engine_versao, meta.regras_versao,
                 meta.modo, meta.datahora, meta.intervalo_inicio,
                 meta.intervalo_fim, meta.ligas, meta.mercados,
                 meta.fixtures_considerados, meta.fixtures_excluidos,
                 meta.previsoes, meta.nota),
            )
            conn.executemany(
                """INSERT INTO bt_predictions
                   (run_id, fixture_id, league_id, league_name, season,
                    home_team, away_team, date, as_of, modo, mercado, linha,
                    prob, confianca, h2h_n, n_home, n_away, sem_stats_home,
                    sem_stats_away, bench_validas, decisao, cobertura_mercado,
                    experimental, odd_real, bookmaker, predicted_edge,
                    predicted_ev, resultado_final, placar_final, total_final,
                    pl_unitario)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                [self._row(p) for p in previsoes],
            )

    @staticmethod
    def _row(p: BacktestPrediction) -> tuple:
        return (
            p.run_id, p.fixture_id, p.league_id, p.league_name, p.season,
            p.home_team, p.away_team, p.date, p.as_of, p.modo, p.mercado,
            p.linha, p.prob, p.confianca, p.h2h_n, p.n_home, p.n_away,
            p.sem_stats_home, p.sem_stats_away, p.bench_validas, p.decisao,
            p.cobertura_mercado, 1 if p.experimental else 0, p.odd_real,
            p.bookmaker, p.predicted_edge, p.predicted_ev, p.resultado_final,
            p.placar_final, p.total_final, p.pl_unitario,
        )

    def listar_runs(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM bt_runs ORDER BY datahora DESC"
            ).fetchall()
        cols = [d[0] for d in conn.execute(
            "SELECT * FROM bt_runs LIMIT 0").description]
        return [dict(zip(cols, r)) for r in rows]

    def previsoes_da_run(self, run_id: str) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM bt_predictions WHERE run_id = ? "
                "ORDER BY id".format(),
                (run_id,),
            ).fetchall()
        cols = [d[0] for d in conn.execute(
            "SELECT * FROM bt_predictions LIMIT 0").description]
        return [dict(zip(cols, r)) for r in rows]


# ----------------------------------------------------------------------
# Engine
# ----------------------------------------------------------------------
@dataclass
class BacktestConfig:
    modo: str = MODO_PRE_GAME         # PRE_GAME / LIVE / PRESSAO
    league_ids: tuple[int, ...] | None = None   # None = todas VIÁVEIS
    mercados: tuple[str, ...] | None = None     # None = todos
    intervalo_inicio: str | None = None         # ISO date
    intervalo_fim: str | None = None
    apenas_viavel: bool = True          # FASE K: respeitar matriz Etapa 4
    incluir_resultado: bool = True      # FASE M: sempre EXPERIMENTAL
    incluir_cartoes: bool = True
    last_n: int = DEFAULT_LAST_N
    limite_fixture: int | None = None   # cap de fixtures (debug)


class BacktestEngine:
    """Executa o backtest sobre o cache. REPRODUTIVEL: mesma config +
    mesmo cache => mesmo resultado. Nao otimiza parametros (FASE G)."""

    def __init__(self, index: CacheIndex, config: BacktestConfig) -> None:
        self.idx = index
        self.cfg = config
        # Versoes das regras congeladas (lidas das fontes, nao hardcoded
        # de resultado): VERSAO_PREJOGO_OP do motor pre-jogo.
        self.regras_versao = VERSAO_PREJOGO_OP

    # -- selecao de fixtures --
    def _fixtures_alvo(self) -> tuple[list[Fixture], list[tuple[Fixture, str]]]:
        """Retorna (considerados, excluidos_com_motivo). Aplica:
          - modo (PRE: encerrados; LIVE_HT: encerrados com 1H;
            PRESSAO: nenhum)
          - intervalo de datas
          - ligas (config ou todas VIÁVEIS da matriz)
          - matriz de cobertura (status_backtest)
          - ter estatistica final cacheada (PRE) / 1H (LIVE_HT)
        """
        if self.cfg.modo == "PRESSAO":
            return [], []
        if self.cfg.modo == MODO_LIVE:
            base = [f for f in self.idx.finished_fixtures()
                    if self.idx.has_1h(f.fixture_id)]
        else:  # PRE
            base = [f for f in self.idx.finished_fixtures()
                    if self.idx.has_stats(f.fixture_id)]

        # Intervalo de datas
        if self.cfg.intervalo_inicio:
            base = [f for f in base
                    if not _antes(f.date, self.cfg.intervalo_inicio,
                                  estrito=False)
                    or f.date is None]
            base = [f for f in base if f.date is None or
                    (_dt(f.date) is not None and
                     _dt(self.cfg.intervalo_inicio) is not None and
                     _dt(f.date) >= _dt(self.cfg.intervalo_inicio))]
        if self.cfg.intervalo_fim:
            base = [f for f in base if f.date is None or
                    (_dt(f.date) is not None and
                     _dt(self.cfg.intervalo_fim) is not None and
                     _dt(f.date) <= _dt(self.cfg.intervalo_fim))]

        # Ligas
        if self.cfg.league_ids is not None:
            lids = set(self.cfg.league_ids)
            base = [f for f in base if f.league_id in lids]
        elif self.cfg.apenas_viavel:
            base = [f for f in base
                    if f.league_id is not None and
                    status_backtest(f.league_id, f.league_name)[0]
                    == BACKTEST_VIAVEL]

        considerados: list[Fixture] = []
        excluidos: list[tuple[Fixture, str]] = []
        for fx in base:
            st, motivo = status_backtest(fx.league_id, fx.league_name)
            if self.cfg.apenas_viavel and st != BACKTEST_VIAVEL:
                excluidos.append((fx, f"backtest {st}: {motivo}"))
                continue
            considerados.append(fx)

        if self.cfg.limite_fixture:
            considerados = considerados[:self.cfg.limite_fixture]
        return considerados, excluidos

    # -- execucao PRE --
    def _run_pre(self, fx: Fixture) -> list[BacktestPrediction]:
        as_of = fx.date  # momento da decisao: antes do kickoff
        games_h, un_h = self.idx.team_history(
            fx.home_team_id, as_of, self.cfg.last_n)
        games_a, un_a = self.idx.team_history(
            fx.away_team_id, as_of, self.cfg.last_n)
        hist = {
            "games_home": games_h,
            "games_away": games_a,
            "n_home": len(games_h),
            "n_away": len(games_a),
            "sem_estatisticas_home": un_h,
            "sem_estatisticas_away": un_a,
        }
        bench_esc = self.idx.league_benchmark_corners(
            fx.league_id, fx.season, as_of, fx.league_name)
        bench_gols = self.idx.league_benchmark_goals(
            fx.league_id, fx.season, as_of, fx.league_name)
        bench_cartoes = self.idx.league_benchmark_cards(
            fx.league_id, fx.season, as_of, fx.league_name)
        h2h_n = self.idx.h2h_n(fx.home_team_id, fx.away_team_id, as_of)

        avaliacoes: list[Any] = []
        if self._mercado_ativo("escanteios") or self._mercado_ativo("gols"):
            avaliacoes += avaliar_pregame(hist, bench_esc, bench_gols, h2h_n)
        if self.cfg.incluir_resultado and self._mercado_ativo("resultado"):
            avaliacoes += avaliar_resultado_prejogo(hist, bench_gols, h2h_n)
        if self.cfg.incluir_cartoes and self._mercado_ativo("cartoes"):
            avaliacoes += avaliar_cartoes_prejogo(
                hist, bench_cartoes, h2h_n=h2h_n)

        # Matriz de cobertura (FASE K): filtra BLOQUEADOS ANTES da
        # aprovacao. PERMITIDO/OBSERVACAO seguem.
        veredictos = avaliar_cobertura_pre(
            fx.league_id, fx.league_name, hist)
        avaliacoes, _bloqueadas = filtrar_avaliacoes_por_cobertura(
            avaliacoes, veredictos)
        aprovadas = aprovar_pregame(avaliacoes)
        aprovadas_set = {id(a) for a in aprovadas}

        # Mapa mercado -> status cobertura (para registro)
        cov_status = {v.mercado: v.status for v in veredictos}

        match = self.idx.match_stats(fx)
        odds = self.idx.odds_for(fx.fixture_id)
        out: list[BacktestPrediction] = []
        for av in avaliacoes:
            mercado_familia = getattr(av, "mercado", "")
            mercado_matriz = MERCADO_DA_FAMILIA.get(mercado_familia, "")
            experimental = mercado_familia == "resultado"
            # Decisao
            if id(av) in aprovadas_set:
                decisao = DEC_ENTRAR
            else:
                # bloqueada por cobertura ja foi removida; restante nao
                # aprovada = fora da janela de prob/conf
                decisao = DEC_AGUARDAR if (
                    0.5 <= av.prob < 0.70 or av.prob > 0.97
                ) else DEC_DESCARTAR
            # Cobertura do mercado
            cov = cov_status.get(mercado_matriz)
            # Odd real
            odd, bm = extrair_odd_real(odds, av.linha, mercado_familia)
            edge, ev = _edge_ev(av.prob, odd)
            # Liquidacao
            resultado, nota = liquidar(av.linha, mercado_familia, fx, match)
            total_final = _total_final(av.linha, fx, match)
            placar = (f"{fx.goals_home}-{fx.goals_away}"
                      if fx.goals_home is not None and fx.goals_away is not None
                      else None)
            pl = _ganho_unitario(resultado or "", odd)
            out.append(BacktestPrediction(
                run_id="", fixture_id=fx.fixture_id,
                league_id=fx.league_id, league_name=fx.league_name,
                season=fx.season, home_team=fx.home_team_name,
                away_team=fx.away_team_name, date=fx.date, as_of=as_of,
                modo="PRE", mercado=mercado_familia, linha=av.linha,
                prob=av.prob, confianca=av.confianca, h2h_n=h2h_n,
                n_home=len(games_h), n_away=len(games_a),
                sem_stats_home=un_h, sem_stats_away=un_a,
                bench_validas=(bench_esc or {}).get("partidas_validas")
                if mercado_familia in ("escanteios", "gols")
                else (bench_cartoes or {}).get("partidas_validas")
                if mercado_familia == "cartoes" else None,
                decisao=decisao, cobertura_mercado=cov,
                experimental=experimental, odd_real=odd, bookmaker=bm,
                predicted_edge=edge, predicted_ev=ev,
                resultado_final=resultado, placar_final=placar,
                total_final=total_final, pl_unitario=pl,
            ))
        return out

    # -- execucao LIVE_HT --
    def _run_live_ht(self, fx: Fixture) -> list[BacktestPrediction]:
        from src.live import LiveSnapshot
        from src.live_opportunity import deep_dive

        as_of = fx.date  # decisao no intervalo (momento HT)
        match = self.idx.match_stats(fx)
        if match is None or match.first_home is None or match.first_away is None:
            return []  # sem 1H honesto -> nao avalia
        # Placar do intervalo: gols do 1T. A API nao separa gols por tempo
        # no fixture; usamos o placar do intervalo via score.halftime quando
        # presente no raw, senao 0-0 (rotulado). Para escanteios/gols do
        # motor live, o que importa e o estado observado (stats_1h).
        raw = self.idx.raw_fixture(fx.fixture_id)
        ht_home = ht_away = 0
        try:
            sc = (raw or {}).get("score") or {}
            ht = sc.get("halftime") or ""
            if "-" in ht:
                a, b = ht.split("-")[:2]
                ht_home, ht_away = int(a), int(b)
        except Exception:
            ht_home = ht_away = 0

        snap = LiveSnapshot(
            fixture_id=fx.fixture_id,
            league_name=fx.league_name or "",
            country="",
            season=(int(fx.season) if fx.season else None),
            round=fx.round,
            date_local=fx.date or "",
            home_team_id=fx.home_team_id,
            home_team_name=fx.home_team_name or "",
            away_team_id=fx.away_team_id,
            away_team_name=fx.away_team_name or "",
            goals_home=ht_home,
            goals_away=ht_away,
            halftime_home=ht_home,
            halftime_away=ht_away,
            status="HT",
            elapsed=45,
            league_id=fx.league_id,
            stats_home=_side_to_dict(match.first_home),
            stats_away=_side_to_dict(match.first_away),
            stats_1h=None,
            stats_2h=None,
            events=[],
            collected_at=fx.date or "",
            has_stats=True,
        )
        client = CacheClient(self.idx, as_of)
        try:
            cand = deep_dive(client, snap, mercados=None, pressao=False)
        except Exception:
            return []

        out: list[BacktestPrediction] = []
        odds = self.idx.odds_for(fx.fixture_id)
        # historico reconstruido para registro (AS_OF)
        games_h, un_h = self.idx.team_history(
            fx.home_team_id, as_of, last=10)
        games_a, un_a = self.idx.team_history(
            fx.away_team_id, as_of, last=10)
        for av in (cand.avaliacoes or []):
            mercado_familia = getattr(av, "mercado", "")
            experimental = mercado_familia == "resultado"
            odd, bm = extrair_odd_real(odds, av.linha, mercado_familia)
            edge, ev = _edge_ev(av.prob, odd)
            resultado, nota = liquidar(av.linha, mercado_familia, fx, match)
            total_final = _total_final(av.linha, fx, match)
            placar = (f"{fx.goals_home}-{fx.goals_away}"
                      if fx.goals_home is not None and fx.goals_away is not None
                      else None)
            pl = _ganho_unitario(resultado or "", odd)
            # Decisao LIVE_HT: o pipeline de aprovacao real (_aprovar)
            # inclui auditoria de status "ainda ao vivo" que exige leitura
            # fresca da API -- NAO reproduzivel honestamente do cache (o
            # status cacheado e o FINAL). Classificamos pelo MESMO
            # criterio de janela prob/conf do motor live, SEM a auditoria
            # de frescor (esta e uma checagem operacional, nao de
            # calibracao estatistica). Documentado como PARCIAL.
            conf = getattr(av, "confianca", 0.0) or 0.0
            if PROB_MIN_APROVAR <= av.prob <= PROB_MAX_APROVAR and \
                    conf >= CONF_MIN_TOP1:
                decisao = DEC_ENTRAR
            elif 0.5 <= av.prob < PROB_MIN_APROVAR or av.prob > PROB_MAX_APROVAR:
                decisao = DEC_AGUARDAR
            else:
                decisao = DEC_DESCARTAR
            out.append(BacktestPrediction(
                run_id="", fixture_id=fx.fixture_id,
                league_id=fx.league_id, league_name=fx.league_name,
                season=fx.season, home_team=fx.home_team_name,
                away_team=fx.away_team_name, date=fx.date, as_of=as_of,
                modo="LIVE_HT", mercado=mercado_familia, linha=av.linha,
                prob=av.prob, confianca=conf,
                h2h_n=getattr(cand, "h2h_n", 0) or 0,
                n_home=len(games_h), n_away=len(games_a),
                sem_stats_home=un_h, sem_stats_away=un_a,
                bench_validas=None,
                decisao=decisao,
                cobertura_mercado=None,
                experimental=experimental, odd_real=odd, bookmaker=bm,
                predicted_edge=edge, predicted_ev=ev,
                resultado_final=resultado, placar_final=placar,
                total_final=total_final, pl_unitario=pl,
            ))
        return out

    def _mercado_ativo(self, familia: str) -> bool:
        if self.cfg.mercados is None:
            return True
        return familia in self.cfg.mercados

    def run(self, run_id: str) -> tuple[BacktestRunMeta, list[BacktestPrediction]]:
        considerados, excluidos = self._fixtures_alvo()
        previsoes: list[BacktestPrediction] = []
        for fx in considerados:
            try:
                if self.cfg.modo == MODO_LIVE:
                    preds = self._run_live_ht(fx)
                else:
                    preds = self._run_pre(fx)
            except Exception:
                # Uma partida que falha nao derruba o run; registra vazio.
                preds = []
            for p in preds:
                p.run_id = run_id
            previsoes += preds

        datas = [f.date for f in considerados if f.date]
        meta = BacktestRunMeta(
            run_id=run_id,
            engine_versao=VERSAO_BACKTEST_ENGINE,
            regras_versao=self.regras_versao,
            modo=self.cfg.modo,
            datahora=datetime.now().isoformat(timespec="seconds"),
            intervalo_inicio=min(datas) if datas else None,
            intervalo_fim=max(datas) if datas else None,
            ligas=",".join(str(l) for l in sorted(
                {f.league_id for f in considerados if f.league_id is not None}
            )),
            mercados=",".join(self.cfg.mercados) if self.cfg.mercados else "todos",
            fixtures_considerados=len(considerados),
            fixtures_excluidos=len(excluidos),
            previsoes=len(previsoes),
            nota=f"excluidos={len(excluidos)}; "
                 f"motivos=" + " | ".join(
                 f"{f.fixture_id}:{m}" for f, m in excluidos[:20])
                 + ("..." if len(excluidos) > 20 else ""),
        )
        return meta, previsoes


def _side_to_dict(s: SideStats) -> dict[str, Any]:
    return {
        "Corner Kicks": s.corners,
        "Total Shots": s.shots,
        "Shots on Goal": s.shots_on_goal,
        "Yellow Cards": s.yellow_cards,
        "Red Cards": s.red_cards,
        "Ball Possession": (f"{s.possession_pct}%" if s.possession_pct is not None else None),
        "Expected Goals": s.expected_goals,
        "Fouls": s.fouls,
        "Offsides": s.offsides,
    }


def _edge_ev(prob: float, odd: float | None) -> tuple[float | None, float | None]:
    """(edge, EV) preditos. Edge = prob - 1/odd (probabilidade justa).
    EV unitario = prob*(odd-1) - (1-prob). Sem odd real => (None, None):
    NUNCA se calcula valor financeiro sem cotacao real (FASE H)."""
    if odd is None or odd <= 0:
        return None, None
    fair = 1.0 / odd
    edge = prob - fair
    ev = prob * (odd - 1.0) - (1.0 - prob)
    return round(edge, 4), round(ev, 4)


def _total_final(linha: str, fx: Fixture, match: MatchStats | None) -> float | None:
    txt = (linha or "").strip()
    parsed = _parse_linha(txt)
    if parsed is None:
        return None
    _, _, unidade = parsed
    if unidade == "gols":
        if fx.goals_home is None or fx.goals_away is None:
            return None
        return float(fx.goals_home + fx.goals_away)
    if unidade == "escanteios":
        if match is None or match.home.corners is None or match.away.corners is None:
            return None
        return float(match.home.corners + match.away.corners)
    if unidade == "cartoes":
        if match is None or any(v is None for v in (
            match.home.yellow_cards, match.away.yellow_cards,
            match.home.red_cards, match.away.red_cards,
        )):
            return None
        return float(
            match.home.yellow_cards + match.away.yellow_cards
            + 2 * (match.home.red_cards + match.away.red_cards)
        )
    return None


# ----------------------------------------------------------------------
# Metricas (FASE H + FASE I)
# ----------------------------------------------------------------------
def _maturidade(n: int) -> str:
    if n < MIN_AMOSTRA_OBSERVACAO:
        return "AMOSTRA INSUFICIENTE"
    if n < MIN_AMOSTRA_MADURA:
        return "EM OBSERVAÇÃO"
    return "AMOSTRA MADURA"


class Metrics:
    """Agrega previsoes de uma run em metricas por corte. NUNCA promove
    regra a validada; apenas rotula maturidade (FASE I)."""

    def __init__(self, previsoes: list[BacktestPrediction]) -> None:
        self.preds = previsoes

    def _settled(self, preds: list[BacktestPrediction]) -> list[BacktestPrediction]:
        return [p for p in preds if p.resultado_final is not None]

    def _ganhas(self, preds: list[BacktestPrediction]) -> list[BacktestPrediction]:
        return [p for p in preds if p.resultado_final == RES_GANHA]

    def resumo_por_mercado(self) -> dict[str, dict[str, Any]]:
        out: dict[str, dict[str, Any]] = {}
        for mercado in sorted({p.mercado for p in self.preds}):
            preds = [p for p in self.preds if p.mercado == mercado]
            out[mercado] = self._corte(preds)
        return out

    def resumo_por_liga(self) -> dict[int, dict[str, Any]]:
        out: dict[int, dict[str, Any]] = {}
        for lid in sorted({p.league_id for p in self.preds if p.league_id}):
            preds = [p for p in self.preds if p.league_id == lid]
            out[lid] = self._corte(preds)
        return out

    def resumo_por_modo(self) -> dict[str, dict[str, Any]]:
        out: dict[str, dict[str, Any]] = {}
        for modo in sorted({p.modo for p in self.preds}):
            preds = [p for p in self.preds if p.modo == modo]
            out[modo] = self._corte(preds)
        return out

    def resumo_por_decisao(self) -> dict[str, dict[str, Any]]:
        out: dict[str, dict[str, Any]] = {}
        for dec in sorted({p.decisao for p in self.preds}):
            preds = [p for p in self.preds if p.decisao == dec]
            out[dec] = self._corte(preds)
        return out

    def resumo_por_faixa_prob(self) -> dict[str, dict[str, Any]]:
        out: dict[str, dict[str, Any]] = {}
        faixas = [("50-60", 0.5, 0.6), ("60-70", 0.6, 0.7),
                  ("70-80", 0.7, 0.8), ("80-90", 0.8, 0.9),
                  ("90-100", 0.9, 1.01)]
        for nome, lo, hi in faixas:
            preds = [p for p in self.preds if lo <= p.prob < hi]
            out[nome] = self._corte(preds)
        return out

    def _corte(self, preds: list[BacktestPrediction]) -> dict[str, Any]:
        total = len(preds)
        settled = self._settled(preds)
        n_settled = len(settled)
        ganhas = self._ganhas(settled)
        perdidas = [p for p in settled if p.resultado_final == RES_PERDIDA]
        devolvidas = [p for p in settled if p.resultado_final == RES_DEVOLVIDA]
        meia_v = [p for p in settled if p.resultado_final == RES_MEIA_VIT]
        meia_d = [p for p in settled if p.resultado_final == RES_MEIA_DER]
        navail = [p for p in preds if p.resultado_final == NAO_AVALIAVEL]
        hit = len(ganhas) / n_settled if n_settled else None
        # ROI apenas onde ha odd real
        com_odd = [p for p in settled if p.odd_real is not None
                   and p.pl_unitario is not None]
        stake = len(com_odd)
        pl = sum(p.pl_unitario for p in com_odd) if com_odd else None
        roi = (pl / stake) if (pl is not None and stake) else None
        # Brier (so settled com resultado binario GANHA/PERDIDA)
        brier = None
        bin_set = [p for p in settled
                   if p.resultado_final in (RES_GANHA, RES_PERDIDA)]
        if bin_set:
            brier = sum(
                (p.prob - (1.0 if p.resultado_final == RES_GANHA else 0.0)) ** 2
                for p in bin_set) / len(bin_set)
        # Calibracao: acerto observado vs prob predita (faixa 70-80 etc.)
        return {
            "total": total,
            "settled": n_settled,
            "ganhas": len(ganhas),
            "perdidas": len(perdidas),
            "devolvidas": len(devolvidas),
            "meia_vitoria": len(meia_v),
            "meia_derrota": len(meia_d),
            "nao_avaliavel": len(navail),
            "taxa_acerto": round(hit, 4) if hit is not None else None,
            "maturidade": _maturidade(n_settled),
            "experimental": any(p.experimental for p in preds),
            "com_odd_real": stake,
            "pl_unitario": round(pl, 4) if pl is not None else None,
            "roi": round(roi, 4) if roi is not None else None,
            "avg_odd": (round(sum(p.odd_real for p in com_odd) / stake, 4)
                        if com_odd else None),
            "brier": round(brier, 4) if brier is not None else None,
        }

    def panorama(self) -> dict[str, Any]:
        return {
            "total_previsoes": len(self.preds),
            "por_mercado": self.resumo_por_mercado(),
            "por_liga": self.resumo_por_liga(),
            "por_modo": self.resumo_por_modo(),
            "por_decisao": self.resumo_por_decisao(),
            "por_faixa_prob": self.resumo_por_faixa_prob(),
        }


# ----------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------
def run_backtest(
    config: BacktestConfig,
    db_path: str = BACKTEST_DB_PATH,
    run_id: str | None = None,
) -> tuple[BacktestRunMeta, list[BacktestPrediction], dict[str, Any]]:
    """Executa um backtest completo e persiste em db_path (SEPARADO do
    ledger operacional). Retorna (meta, previsoes, panorama_metricas)."""
    idx = CacheIndex()
    idx.load()
    engine = BacktestEngine(idx, config)
    rid = run_id or f"bt-{datetime.now().strftime('%Y%m%d%H%M%S')}-{config.modo}"
    meta, preds = engine.run(rid)
    store = BacktestStore(db_path)
    store.salvar_run(meta, preds)
    panorama = Metrics(preds).panorama()
    return meta, preds, panorama


def _main() -> None:
    parser = argparse.ArgumentParser(
        description="Engine de backtest dedicado do Corner Intelligence.")
    sub = parser.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run", help="Executa um backtest.")
    r.add_argument("--modo", default=MODO_PRE_GAME,
                   choices=[MODO_PRE_GAME, MODO_LIVE, "PRESSAO"])
    r.add_argument("--ligas", default=None,
                   help="CSV de IDs de liga (default: todas VIÁVEIS).")
    r.add_argument("--mercados", default=None,
                   help="CSV de familias (escanteios,gols,cartoes,resultado).")
    r.add_argument("--inicio", default=None, help="ISO date (inclusive).")
    r.add_argument("--fim", default=None, help="ISO date (inclusive).")
    r.add_argument("--limite", type=int, default=None,
                   help="Cap de fixtures (debug).")
    r.add_argument("--saida", default=BACKTEST_DB_PATH,
                   help="Caminho do DB do backtest.")
    r.add_argument("--sem-viavel", action="store_true",
                   help="Nao filtrar so VIÁVEIS (usa todas as ligas).")
    r.add_argument("--panorama", action="store_true",
                   help="Imprimir panorama de metricas.")

    s = sub.add_parser("runs", help="Lista runs gravados.")
    s.add_argument("--saida", default=BACKTEST_DB_PATH)

    args = parser.parse_args()

    if args.cmd == "runs":
        store = BacktestStore(args.saida)
        for row in store.listar_runs():
            print(f"{row['run_id']}  modo={row['modo']}  "
                  f"fx={row['fixtures_considerados']}  "
                  f"preds={row['previsoes']}  {row['datahora']}")
        return

    # cmd == run
    league_ids = None
    if args.ligas:
        league_ids = tuple(int(x) for x in args.ligas.split(",") if x)
    mercados = None
    if args.mercados:
        mercados = tuple(x.strip() for x in args.mercados.split(","))
    config = BacktestConfig(
        modo=args.modo, league_ids=league_ids, mercados=mercados,
        intervalo_inicio=args.inicio, intervalo_fim=args.fim,
        apenas_viavel=not args.sem_viavel,
        limite_fixture=args.limite,
    )
    meta, preds, pan = run_backtest(config, db_path=args.saida)
    print(f"RUN {meta.run_id}")
    print(f"  engine={meta.engine_versao}  regras={meta.regras_versao}")
    print(f"  modo={meta.modo}  intervalo={meta.intervalo_inicio}..{meta.intervalo_fim}")
    print(f"  ligas={meta.ligas}")
    print(f"  fixtures considerados={meta.fixtures_considerados}  "
          f"excluidos={meta.fixtures_excluidos}")
    print(f"  previsoes={meta.previsoes}")
    if args.panorama:
        print(json.dumps(pan, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    _main()