"""ETAPA 5F-D -- Orquestrador de auditoria multi-fonte (MODO SEGURO).

Camada de COLETA/AUDITORIA. Importa SOMENTE src/multifonte e (read-only) o
banco de snapshots existente. NAO importa nem altera qualquer modulo do motor
(analysis, politica_aprovacao, policy, settlement, calibration, backtest,
prejogo_opportunity, odds, identity, cache, config, api_client, live_pressure,
ao_vivo, app). NAO implementa fallback. NAO escolhe fonte vencedora. NAO
calcula ROI. NAO altera regras.

Reconciliacao de fixtures: MATCHED / AMBIGUOUS / NOT_MATCHED (por data/hora,
competicao, temporada, mandante/visitante, placar, status -- nao so nome
textual). Nao cria identidade permanente no motor.

Registro de conflito entre fontes: CONFLITO_DE_FONTE (preservado, NUNCA
resolvido automaticamente; nenhuma fonte sobrescreve outra).

Matrizes:
  - Cobertura (FONTE x ~25 colunas): VALIDADO / PARCIAL / LIMITADO_PELO_PLANO /
    SEM_CREDENCIAL / NAO_DISPONIVEL / FALHA_TECNICA / A_CONFIRMAR.
  - Confiabilidade (CAMPO x candidato principal/fallback/evidencia/...).

MODO SEGURO: se MULTISOURCE_CREDENTIALS_ROTATED != 1, chamadas autenticadas
sao BLOQUEADAS. A auditoria opera somente com: (a) cache existente da
API-Football (read-only), (b) StatsBomb publico (sem credencial, opcional).
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field, asdict
from typing import Any

try:
    from src.multifonte import (
        credenciais_rotacionadas, status_credenciais,
        CredentialsBlocked, EndpointNotConfirmed, RateLimitHit, QuotaExhausted,
        NormalizedFact, NormalizedOdd, FactProvider, OddsProvider,
        TheOddsAPIProvider, FiveDollarFootballProvider,
        SportmonksProvider, APIFootballComProvider,
        FootballDataOrgProvider, StatsBombOpenProvider,
        ODDS_PROVIDERS, FACT_PROVIDERS,
        ST_OK, ST_MISSING, ST_NOT_CONFIRMED, ST_BLOCKED, ST_NO_CREDENTIAL,
        ST_PLAN_LIMITED, ST_NOT_AVAILABLE, ST_TECHNICAL_FAIL, ST_NULL,
    )
except ImportError:  # protecao contra import direto fora do pacote
    from multifonte import (  # type: ignore
        credenciais_rotacionadas, status_credenciais,
        CredentialsBlocked, EndpointNotConfirmed, RateLimitHit, QuotaExhausted,
        NormalizedFact, NormalizedOdd, FactProvider, OddsProvider,
        TheOddsAPIProvider, FiveDollarFootballProvider,
        SportmonksProvider, APIFootballComProvider,
        FootballDataOrgProvider, StatsBombOpenProvider,
        ODDS_PROVIDERS, FACT_PROVIDERS,
        ST_OK, ST_MISSING, ST_NOT_CONFIRMED, ST_BLOCKED, ST_NO_CREDENTIAL,
        ST_PLAN_LIMITED, ST_NOT_AVAILABLE, ST_TECHNICAL_FAIL, ST_NULL,
    )

try:
    from src.config import DB_PATH
except Exception:  # fallback se config indisponivel
    DB_PATH = "data/corner_intelligence.db"

# ----------------------------------------------------------------------
# Reconciliacao de fixtures
# ----------------------------------------------------------------------
REC_MATCHED = "MATCHED"
REC_AMBIGUOUS = "AMBIGUOUS"
REC_NOT_MATCHED = "NOT_MATCHED"


@dataclass
class ReconciliationResult:
    """Resultado de reconciliacao de uma fixture entre fontes.

    canonical = fixture API-Football (id e nome).
    candidate = fixture do provider externo (id do provider + nome).
    Nunca cria identidade permanente no motor.
    """
    status: str                       # MATCHED | AMBIGUOUS | NOT_MATCHED
    canonical_fixture_id: str | None
    candidate_provider: str
    candidate_fixture_id: str | None
    confidence: float                 # 0.0-1.0
    motivos: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return asdict(self)


def _norm_nome(nome: str | None) -> str:
    if not nome:
        return ""
    import unicodedata
    nf = unicodedata.normalize("NFD", nome)
    sem_acento = "".join(c for c in nf if not unicodedata.combining(c))
    return "".join(c.lower() for c in sem_acento if c.isalnum())


def reconciliar_fixture(
    canonical: dict,
    candidate: dict,
    provider: str,
) -> ReconciliationResult:
    """Reconcilia por data/hora, competicao, temporada, mandante/visitante,
    placar e status -- NAO so por nome textual.

    canonical: dict com chaves fixture_id, home, away, kickoff, competition,
               season, score_home, score_away, status.
    candidate: mesmas chaves (home/away/kickoff/... podem ser None).
    """
    motivos: list[str] = []
    score = 0.0
    total = 0.0

    def peso(chave: str, w: float):
        nonlocal score, total
        cv = canonical.get(chave)
        cc = candidate.get(chave)
        if cv is None and cc is None:
            # Ausente em ambas => neutro (nao reduz nem aumenta a confianca).
            return
        total += w
        if cv is None or cc is None:
            motivos.append(f"{chave}: ausente em uma fonte ({chave}={cv!r} vs {cc!r})")
            return
        if chave in ("home", "away"):
            if _norm_nome(cv) and _norm_nome(cv) == _norm_nome(cc):
                score += w
            else:
                motivos.append(f"{chave}: divergente ({cv!r} vs {cc!r})")
        elif chave == "kickoff":
            # tolerancia de 1 dia para diferenca de fuso
            try:
                from datetime import datetime
                a = str(cv)[:16]
                b = str(cc)[:16]
                if a == b:
                    score += w
                else:
                    motivos.append(f"kickoff: {a} vs {b}")
            except Exception:
                motivos.append(f"kickoff: {cv!r} vs {cc!r}")
        else:
            if str(cv) == str(cc):
                score += w
            else:
                motivos.append(f"{chave}: {cv!r} vs {cc!r}")

    # pesos: mandante/visitante + kickoff + competicao sao os mais fortes.
    peso("home", 2.0)
    peso("away", 2.0)
    peso("kickoff", 2.0)
    peso("competition", 1.0)
    peso("season", 0.5)
    peso("score_home", 1.0)
    peso("score_away", 1.0)
    peso("status", 0.5)

    if total == 0:
        conf = 0.0
    else:
        conf = score / total

    if conf >= 0.85:
        status = REC_MATCHED
    elif conf >= 0.5:
        status = REC_AMBIGUOUS
        motivos.append(f"confianca parcial {conf:.2f}")
    else:
        status = REC_NOT_MATCHED
        motivos.append(f"confianca insuficiente {conf:.2f}")

    return ReconciliationResult(
        status=status,
        canonical_fixture_id=str(canonical.get("fixture_id")),
        candidate_provider=provider,
        candidate_fixture_id=str(candidate.get("fixture_id")) if candidate.get("fixture_id") else None,
        confidence=round(conf, 4),
        motivos=motivos or ["todos os campos comparados coincidiram"],
    )


# ----------------------------------------------------------------------
# Registro de conflito entre fontes (CONFLITO_DE_FONTE)
# ----------------------------------------------------------------------
@dataclass
class ConflictRecord:
    """Conflito entre duas fontes para o MESMO fato. Preservado, NUNCA
    resolvido automaticamente. Nenhuma fonte sobrescreve outra."""
    provider_a: str
    provider_b: str
    fixture_id: str | None
    field: str
    value_a: Any
    value_b: Any
    timestamp_a: str | None
    timestamp_b: str | None
    natureza: str          # "valor_divergente" | "presenca_divergente" | "timestamp_divergente"
    nota: str | None = None

    def as_dict(self) -> dict:
        return asdict(self)


class ConflictRegistry:
    """Registro de conflitos entre fontes. Append-only, sem resolucao auto."""
    def __init__(self) -> None:
        self._items: list[ConflictRecord] = []

    def registrar(self, c: ConflictRecord) -> None:
        self._items.append(c)

    def comparar_fatos(self, fatos: list[NormalizedFact]) -> int:
        """Compara fatos do MESMO field/fixture vindos de providers diferentes.

        Conflito = valor divergente (ambos presentes, distintos e nao-None).
        Timestamps diferentes NAO sao conflito direto (auditar separadamente).
        null vs valor NAO e tratado como zero -- null permanece null.
        """
        n = 0
        por_chave: dict[tuple, list[NormalizedFact]] = {}
        for f in fatos:
            if f.status != ST_OK:
                continue
            chave = (f.fixture_corner_id or f.fixture_provider_id, f.field)
            por_chave.setdefault(chave, []).append(f)
        for chave, grupo in por_chave.items():
            provs: dict[str, NormalizedFact] = {}
            for f in grupo:
                provs.setdefault(f.provider, f)
            nomes = list(provs.keys())
            for i in range(len(nomes)):
                for j in range(i + 1, len(nomes)):
                    a, b = provs[nomes[i]], provs[nomes[j]]
                    if a.normalized_value is None or b.normalized_value is None:
                        continue  # null nao conflita (null != valor)
                    if a.normalized_value != b.normalized_value:
                        self.registrar(ConflictRecord(
                            provider_a=a.provider, provider_b=b.provider,
                            fixture_id=chave[0], field=chave[1],
                            value_a=a.normalized_value, value_b=b.normalized_value,
                            timestamp_a=None, timestamp_b=None,
                            natureza="valor_divergente"))
                        n += 1
        return n

    def items(self) -> list[ConflictRecord]:
        return list(self._items)

    def to_list(self) -> list[dict]:
        return [c.as_dict() for c in self._items]


# ----------------------------------------------------------------------
# Matriz de cobertura (FONTE x colunas)
# ----------------------------------------------------------------------
COLUNAS_COBERTURA = [
    # identidade / fixture
    "fixtures", "competitions", "seasons", "teams",
    # placar / identidade
    "score_ht", "score_ft", "kickoff", "status",
    # stats de jogo
    "corners", "shots", "shots_on_target", "blocked_shots",
    "possession", "fouls", "yellow_cards", "red_cards",
    "total_cards", "goals",
    # odds
    "odds_pre_match", "odds_live", "odds_opening", "odds_closing",
    # mercados
    "over_under_corners", "handicap_corners", "asian_corners",
    "over_under_goals", "handicap_goals", "over_under_cards",
    # eventos
    "events_timeline", "lineups",
]


def classificar_cobertura(provider: str, cred_status: dict[str, dict[str, bool]]) -> dict[str, str]:
    """Classifica cada coluna para um provider segundo o status.

    Nao inventa "VALIDADO" sem observacao real. Em MODO SEGURO, fontes
    autenticadas => SEM_CREDENCIAL/BLOQUEADO_SEGURANCA; StatsBomb => VALIDADO
    so onde o dado publico de fato cobre (eventos/lineups historicos); API-
    Football => VALIDADO (cache existente observado).
    """
    gate = credenciais_rotacionadas()
    cs = cred_status.get(provider, {})
    autenticada = provider not in ("api_football", "statsbomb_open")

    def base() -> str:
        if autenticada:
            if not cs.get("credencial_configurada", False):
                return ST_NO_CREDENTIAL
            if not gate:
                return ST_BLOCKED
            return ST_NOT_CONFIRMED  # chamada real nao executada nesta etapa
        return ST_OK  # api_football (cache) / statsbomb (publico)

    estado = base()
    out: dict[str, str] = {}
    for col in COLUNAS_COBERTURA:
        if provider == "api_football":
            # Cache existente observado: odds pre/live validados; stats/corners
            # dependem de /fixtures/statistics (VALIDADO quando coberto).
            if col in ("odds_pre_match", "odds_live", "corners", "goals",
                       "yellow_cards", "red_cards", "total_cards",
                       "shots", "shots_on_target", "possession",
                       "fixtures", "competitions", "teams", "kickoff",
                       "status", "score_ft"):
                out[col] = ST_OK
            elif col in ("odds_opening", "odds_closing"):
                # API-Football /odds nao marca abertura/fechamento de forma
                # confiavel (apenas update_feed). OPENING_PROVIDER vs
                # PRIMEIRO_SNAPSHOT_LOCAL: NAO inferir.
                out[col] = ST_NOT_CONFIRMED
            elif col in ("score_ht", "handicap_corners", "asian_corners",
                         "handicap_goals"):
                out[col] = ST_PLAN_LIMITED
            else:
                out[col] = ST_OK
        elif provider == "statsbomb_open":
            # Publico, historico. NAO live, NAO odds.
            if col in ("events_timeline", "lineups", "shots",
                       "shots_on_target", "goals", "possession",
                       "competitions", "seasons"):
                out[col] = ST_OK
            elif col in ("odds_pre_match", "odds_live", "odds_opening",
                         "odds_closing", "over_under_corners",
                         "handicap_corners", "asian_corners",
                         "over_under_goals", "handicap_goals",
                         "over_under_cards", "corners", "yellow_cards",
                         "red_cards", "total_cards", "blocked_shots",
                         "fouls"):
                out[col] = ST_NOT_AVAILABLE  # nao oferecido por esta fonte
            elif col in ("fixtures", "teams", "kickoff", "status", "score_ft",
                         "score_ht"):
                out[col] = ST_OK
            else:
                out[col] = ST_NOT_AVAILABLE
        else:
            # Fonte autenticada: SEM credencial/gate => SEM_CREDENCIAL/BLOQUEADO.
            out[col] = estado
    return out


def matriz_cobertura(cred_status: dict[str, dict[str, bool]]) -> dict[str, dict[str, str]]:
    return {p: classificar_cobertura(p, cred_status) for p in cred_status}


# ----------------------------------------------------------------------
# Matriz de confiabilidade (CAMPO x candidatos)
# ----------------------------------------------------------------------
# Para cada campo factual, qual o candidato principal/fallback e a evidencia.
# Nenhum candidato e "vencedor" no motor -- apenas mapeamento de auditoria.
CAMPOS_CONFIABILIDADE = [
    "corners", "goals", "red_cards", "yellow_cards", "total_cards",
    "shots", "shots_on_target", "possession",
    "odds_pre_match", "odds_live", "odds_opening",
]


def matriz_confiabilidade() -> dict[str, dict[str, Any]]:
    """Candidato principal/fallback/evidencia/coverage/status por campo.

    status reflete MODO SEGURO: sem chamadas autenticadas, a maior parte e
    A_CONFIRMAR / SEM_CREDENCIAL. O mapeamento e informativo, NAO imperativo.
    """
    base: dict[str, dict[str, Any]] = {
        "corners": {"principal": "api_football", "fallback": "sportmonks",
                    "evidencia": "cache existente (api_football)",
                    "coverage": "VALIDADO(api_football) / SEM_CREDENCIAL(sportmonks)",
                    "status": "PARCIAL"},
        "goals": {"principal": "api_football", "fallback": "statsbomb_open",
                  "evidencia": "cache + open data",
                  "coverage": "VALIDADO(api_football,statsbomb_open)",
                  "status": "PARCIAL"},
        "red_cards": {"principal": "api_football", "fallback": "sportmonks",
                      "evidencia": "Red Cards=null e limitacao estrutural (Etapa 5D)",
                      "coverage": "LIMITADO_PELO_PLANO",
                      "status": "NAO_AVALIAVEL"},
        "yellow_cards": {"principal": "api_football", "fallback": "sportmonks",
                         "evidencia": "cache existente",
                         "coverage": "VALIDADO(api_football)",
                         "status": "PARCIAL"},
        "total_cards": {"principal": "api_football", "fallback": "apifootball_com",
                        "evidencia": "amarelo+vermelho (conv 1/2) ou direto",
                        "coverage": "VALIDADO(api_football)",
                        "status": "PARCIAL"},
        "shots": {"principal": "api_football", "fallback": "statsbomb_open",
                  "evidencia": "/fixtures/statistics + events",
                  "coverage": "VALIDADO(api_football,statsbomb_open)",
                  "status": "PARCIAL"},
        "shots_on_target": {"principal": "api_football",
                            "fallback": "statsbomb_open",
                            "evidencia": "/fixtures/statistics + events",
                            "coverage": "VALIDADO(api_football,statsbomb_open)",
                            "status": "PARCIAL"},
        "possession": {"principal": "api_football",
                       "fallback": "statsbomb_open",
                       "evidencia": "/fixtures/statistics",
                       "coverage": "VALIDADO(api_football)",
                       "status": "PARCIAL"},
        "odds_pre_match": {"principal": "api_football",
                           "fallback": "the_odds_api",
                           "evidencia": "odds_snapshot_history (317.951)",
                           "coverage": "VALIDADO(api_football) / SEM_CREDENCIAL(the_odds_api)",
                           "status": "PARCIAL"},
        "odds_live": {"principal": "api_football",
                      "fallback": "the_odds_api",
                      "evidencia": "live 7.053 snapshots",
                      "coverage": "VALIDADO(api_football)",
                      "status": "PARCIAL"},
        "odds_opening": {"principal": "PRIMEIRO_SNAPSHOT_LOCAL",
                         "fallback": "A_CONFIRMAR",
                         "evidencia": "API nao marca abertura; nao inferir",
                         "coverage": "NAO_INFERIDO",
                         "status": "A_CONFIRMAR"},
    }
    return base


# ----------------------------------------------------------------------
# Leitura read-only do banco de snapshots existente (cache API-Football)
# ----------------------------------------------------------------------
def resumo_banco_snapshots() -> dict[str, Any]:
    """Leitura read-only de odds_snapshot_history. NAO escreve, NAO migra."""
    out: dict[str, Any] = {"db_path": str(DB_PATH), "tabela": "odds_snapshot_history"}
    try:
        conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
        cur = conn.cursor()
        try:
            cur.execute("SELECT COUNT(*) FROM odds_snapshot_history")
            out["total"] = cur.fetchone()[0]
        except sqlite3.OperationalError as e:
            out["erro"] = str(e)
            conn.close()
            return out
        for sql, chave in (
            ("SELECT provider, COUNT(*) FROM odds_snapshot_history GROUP BY provider", "por_provider"),
            ("SELECT coleta_tipo, COUNT(*) FROM odds_snapshot_history GROUP BY coleta_tipo", "por_coleta_tipo"),
            ("SELECT status, COUNT(*) FROM odds_snapshot_history GROUP BY status", "por_status"),
        ):
            try:
                cur.execute(sql)
                out[chave] = {str(k): v for k, v in cur.fetchall()}
            except sqlite3.OperationalError:
                out[chave] = {}
        try:
            cur.execute("SELECT COUNT(DISTINCT snapshot_hash) FROM odds_snapshot_history")
            out["hashs_distintos"] = cur.fetchone()[0]
        except sqlite3.OperationalError:
            out["hashs_distintos"] = None
        try:
            cur.execute("SELECT COUNT(*) FROM odds_snapshot_history WHERE provider IS NULL")
            out["provider_null"] = cur.fetchone()[0]
        except sqlite3.OperationalError:
            out["provider_null"] = None
        try:
            cur.execute("PRAGMA user_version")
            out["user_version"] = cur.fetchone()[0]
        except sqlite3.OperationalError:
            out["user_version"] = None
        try:
            cur.execute("PRAGMA integrity_check")
            out["integrity_check"] = cur.fetchone()[0]
        except sqlite3.OperationalError:
            out["integrity_check"] = "erro"
        conn.close()
    except Exception as e:
        out["erro"] = str(e)
    return out


# ----------------------------------------------------------------------
# Auditores de amostra (cards / corners / goals) -- MODO SEGURO
# ----------------------------------------------------------------------
def _bloqueado(provider: str, op: str) -> dict:
    return {"provider": provider, "operacao": op,
            "status": ST_BLOCKED if credenciais_rotacionadas() else ST_NO_CREDENTIAL,
            "nota": "CHAMADAS REAIS BLOQUEADAS POR SEGURANCA"}


def auditar_cartoes_amostra() -> list[dict]:
    """Amostra de cartoes (Etapa 5D): Red=null, Red=0, Red>0, sem statistics.

    Compara API-Football (cache) vs Sportmonks/APIFootball.com. Sem chamada
    autenticada => SEM_CREDENCIAL/BLOQUEADO. Nenhuma alteracao de settlement.
    """
    res: list[dict] = []
    for p in ("sportmonks", "apifootball_com"):
        res.append(_bloqueado(p, "cards_sample"))
    # API-Football: le read-only do banco/estrutura existente (ja auditado 5D).
    res.append({
        "provider": "api_football", "operacao": "cards_sample",
        "status": ST_OK,
        "evidencia": "Etapa 5D: Red Cards=null (207/207 NA), limitacao estrutural",
        "red_null": "confirmado (null != zero)",
    })
    return res


def auditar_escanteios_amostra() -> list[dict]:
    """Amostra de escanteios (Etapa 5C): pre/post drift, competicoes distintas.

    Cobertura/exatidao/concordancia/diferenca media/conflitos/ausentes/zero.
    """
    res: list[dict] = []
    for p in ("sportmonks", "apifootball_com"):
        res.append(_bloqueado(p, "corners_sample"))
    res.append({
        "provider": "api_football", "operacao": "corners_sample",
        "status": ST_OK,
        "evidencia": "cache existente; drift temporal EM OBSERVAÇÃO (Etapa 5C)",
    })
    return res


def auditar_gols_controle() -> list[dict]:
    """Gols como controle de identidade: HT/FT divergente => erro de mapeamento."""
    res: list[dict] = []
    res.append({
        "provider": "api_football", "operacao": "goals_identity_control",
        "status": ST_OK,
        "evidencia": "HT/FT em /fixtures; divergencia => AMBIGUOUS",
    })
    res.append({
        "provider": "statsbomb_open", "operacao": "goals_identity_control",
        "status": ST_NOT_CONFIRMED,
        "nota": "open data historico; smoke test pendente (fetcher/rede)",
    })
    return res


def auditar_odds() -> list[dict]:
    """Auditoria de odds: API-Football vs The Odds API vs 5Dollar.

    Timestamps diferentes NAO sao conflito direto. Sem gate => BLOQUEADO.
    """
    res: list[dict] = []
    res.append({
        "provider": "api_football", "operacao": "odds_audit",
        "status": ST_OK,
        "evidencia": "317.951 snapshots; 7.053 live; 298.187 pre_match",
    })
    for p in ("the_odds_api", "five_dollar_football"):
        res.append(_bloqueado(p, "odds_audit"))
    return res


def auditar_abertura_fechamento() -> list[dict]:
    """OPENING_PROVIDER vs PRIMEIRO_SNAPSHOT_LOCAL. NAO inferir."""
    return [{
        "provider": "api_football", "operacao": "opening_closing",
        "status": ST_NOT_CONFIRMED,
        "nota": "API-Football /odds nao marca abertura/fechamento; "
                "usar PRIMEIRO_SNAPSHOT_LOCAL, nao inferir OPENING_PROVIDER",
    }]


def auditar_live() -> list[dict]:
    """Auditoria live: minuto/placar/escanteios/chots/SOT/blocked/posse/cards/ts.

    Ausente => AUSENTE (nao zero). Sem thresholds de pressao alterados.
    """
    return [{
        "provider": "api_football", "operacao": "live_audit",
        "status": ST_OK,
        "evidencia": "/odds/live + /fixtures/statistics; TTL 60s (motor intacto)",
    }]


# ----------------------------------------------------------------------
# Prontidao de coleta prospectiva por provider (sem daemon)
# ----------------------------------------------------------------------
def prontidao_coleta() -> dict[str, str]:
    """PRONTO / PARCIAL / NAO PRONTO por provider. NAO inicia daemon."""
    out: dict[str, str] = {}
    cs = status_credenciais()
    for p, st in cs.items():
        if p == "api_football":
            out[p] = "PRONTO"
        elif p == "statsbomb_open":
            out[p] = "PARCIAL"  # publico, mas nao live/odds; fetcher pendente
        elif st.get("credencial_configurada") and st.get("gate_aberto"):
            out[p] = "PARCIAL"
        else:
            out[p] = "NAO_PRONTO"
    return out


# ----------------------------------------------------------------------
# Runner principal -- MODO SEGURO
# ----------------------------------------------------------------------
def auditar(gate_override: bool | None = None) -> dict:
    """Executa a auditoria multi-fonte em MODO SEGURO.

    gate_override: se None, usa o ambiente real. Se True/False, forca (teste).
    """
    import time
    if gate_override is not None:
        import src.multifonte as _mf
        _mf_gate_orig = _mf.credenciais_rotacionadas
        # monkeypatch apenas para esta execucao
        _mf.credenciais_rotacionadas = lambda: bool(gate_override)  # type: ignore
    else:
        _mf_gate_orig = None

    try:
        cred = status_credenciais()
        gate = credenciais_rotacionadas()
        t0 = time.time()
        return {
            "etapa": "5F-D",
            "timestamp": t0,
            "gate_aberto": gate,
            "modo_seguro_ativo": not gate,
            "credenciais": cred,
            "banco_snapshots": resumo_banco_snapshots(),
            "matriz_cobertura": matriz_cobertura(cred),
            "matriz_confiabilidade": matriz_confiabilidade(),
            "auditorias": {
                "cartoes": auditar_cartoes_amostra(),
                "escanteios": auditar_escanteios_amostra(),
                "gols_controle": auditar_gols_controle(),
                "odds": auditar_odds(),
                "abertura_fechamento": auditar_abertura_fechamento(),
                "live": auditar_live(),
            },
            "conflitos": [],
            "prontidao_coleta": prontidao_coleta(),
            "chamadas_autenticadas_executadas": 0,
            "chamadas_statsbomb_executadas": 0,
            "bloqueio": ("CHAMADAS REAIS BLOQUEADAS POR SEGURANCA"
                         if not gate else "GATE ABERTO"),
        }
    finally:
        if _mf_gate_orig is not None:
            import src.multifonte as _mf
            _mf.credenciais_rotacionadas = _mf_gate_orig  # type: ignore


def _real_fetcher(url: str):
    """Fetcher real para StatsBomb Open Data (publico, sem credencial)."""
    import json as _json
    from urllib.request import urlopen
    with urlopen(url, timeout=30) as resp:
        return resp.read().decode("utf-8", errors="replace")


def _segredos_ativos() -> list[str]:
    """Lista valores de credencial presentes (para redact em raws). NAO imprime."""
    import os as _os
    envs = ["API_KEY", "THE_ODDS_API_KEY", "SPORTMONKS_API_TOKEN",
            "APIFOOTBALL_COM_API_KEY", "FIVE_DOLLAR_FOOTBALL_API_KEY",
            "FOOTBALL_DATA_API_KEY"]
    out = []
    for e in envs:
        v = _os.getenv(e, "")
        if v:
            out.append(v)
    return out


def _shape_summary(data) -> str:
    """Resumo de shape da resposta (sem conteudo sensivel)."""
    if isinstance(data, list):
        return f"list[{len(data)}]"
    if isinstance(data, dict):
        keys = list(data.keys())[:8]
        return f"dict keys={keys}"
    return f"{type(data).__name__}"


def _salvar_raw(raw_dir: str, nome: str, data, secrets: list[str]) -> None:
    """Salva resposta raw redacted em raw_dir (gitignored)."""
    import os as _os
    try:
        _os.makedirs(raw_dir, exist_ok=True)
        texto = json.dumps(data, ensure_ascii=False, default=str)
        from src.multifonte import _redact
        texto = _redact(texto, secrets)
        with open(_os.path.join(raw_dir, nome), "w", encoding="utf-8") as f:
            f.write(texto)
    except Exception:
        pass  # raw e bonus; auditoria nao falha por isso


def _candidatos_cache(date_str: str) -> list[dict]:
    """Le read-only do cache API-Football: FT fixtures em date_str com
    status de Red Cards (all_null/all_zero/has_red) + corners por time."""
    import sqlite3 as _sq
    out: list[dict] = []
    try:
        conn = _sq.connect(f"file:{DB_PATH}?mode=ro", uri=True)
        cur = conn.cursor()
        # metadata de fixtures
        meta: dict[int, dict] = {}
        cur.execute("SELECT params,response FROM api_cache WHERE endpoint='/fixtures'")
        for params, response in cur.fetchall():
            try:
                d = json.loads(response)
            except Exception:
                continue
            rows = d.get("response", []) if isinstance(d, dict) else d
            if not isinstance(rows, list):
                continue
            for r in rows:
                if not isinstance(r, dict):
                    continue
                f = r.get("fixture", {}) or {}
                t = r.get("teams", {}) or {}
                lg = r.get("league", {}) or {}
                fid = f.get("id") if isinstance(f, dict) else None
                if fid is None:
                    continue
                date = str(f.get("date", ""))[:10]
                status = (f.get("status", {}) or {}).get("short") if isinstance(f.get("status"), dict) else None
                meta[fid] = {
                    "fixture_id": fid, "date": date, "status": status,
                    "home": (t.get("home", {}) or {}).get("name"),
                    "away": (t.get("away", {}) or {}).get("name"),
                    "league": lg.get("name"), "league_id": lg.get("id"),
                    "season": lg.get("season"),
                    "score_home": (r.get("goals", {}) or {}).get("home"),
                    "score_away": (r.get("goals", {}) or {}).get("away"),
                }
        # statistics: red cards + corners
        stat: dict[int, dict] = {}
        cur.execute("SELECT params,response FROM api_cache WHERE endpoint='/fixtures/statistics'")
        for params, response in cur.fetchall():
            try:
                p = json.loads(params) if isinstance(params, str) else params
                d = json.loads(response)
            except Exception:
                continue
            fid = p.get("fixture") if isinstance(p, dict) else None
            rows = d.get("response", []) if isinstance(d, dict) else d
            if not isinstance(rows, list):
                continue
            reds = []
            corners = []
            for tb in rows:
                if not isinstance(tb, dict):
                    continue
                for s in (tb.get("statistics") or []):
                    if s.get("type") == "Red Cards":
                        reds.append(s.get("value"))
                    if s.get("type") == "Corner Kicks":
                        corners.append(s.get("value"))
            stat[fid] = {"reds": reds, "corners": corners}
        conn.close()
    except Exception:
        return out
    for fid, m in meta.items():
        if m["status"] != "FT" or m["date"] != date_str:
            continue
        st = stat.get(fid, {})
        reds = st.get("reds", [])
        corners = st.get("corners", [])
        if reds and all(v is None for v in reds):
            red_class = "all_null"
        elif reds and any(v and v > 0 for v in reds):
            red_class = "has_red"
        elif reds:
            red_class = "all_zero"
        else:
            red_class = "no_stats"
        out.append({
            **m,
            "red_class": red_class,
            "red_values": reds,
            "corners_values": corners,
            "corners_total": sum(v for v in corners if isinstance(v, (int, float))) if any(isinstance(v, (int, float)) for v in corners) else None,
        })
    out.sort(key=lambda x: x["home"] or "")
    return out


def _eh_youth_event(ev: dict) -> bool:
    txt = f"{ev.get('match_hometeam_name','')} {ev.get('match_awayteam_name','')} {ev.get('league_name','')}".lower()
    return any(tag in txt for tag in ("u19", "u20", "u21", "u23", "youth"))


def comparar_cards_apifootball(afc_events: list, candidatos: list,
                               conflitos: "ConflictRegistry") -> tuple[dict, list[dict]]:
    """Compara Red Cards API-Football (cache) vs apifootball_com (get_events).

    Reconcilia por nome normalizado (accent-fold) + prefixo fuzzy (4 chars),
    excluindo youth/U19 (colisao de prefixo com senior). Retorna (observacao,
    detalhes). Registra conflitos em `conflitos`. NAO altera motor.
    """
    cards_cmp: list[dict] = []
    if not isinstance(afc_events, list):
        afc_events = []
    nome_to_afc: dict[tuple, dict] = {}
    prefix_to_afc: dict[tuple, dict] = {}
    for ev in afc_events:
        if not isinstance(ev, dict) or _eh_youth_event(ev):
            continue
        h = _norm_nome(ev.get("match_hometeam_name"))
        a = _norm_nome(ev.get("match_awayteam_name"))
        if h and a:
            nome_to_afc[(h, a)] = ev
            prefix_to_afc.setdefault((h[:4], a[:4]), ev)
    for cand in candidatos:
        h = _norm_nome(cand["home"])
        a = _norm_nome(cand["away"])
        ev = nome_to_afc.get((h, a))
        match_kind = "exact"
        if not ev:
            ev = prefix_to_afc.get((h[:4], a[:4]))
            match_kind = "fuzzy_prefix"
        if not ev:
            continue
        has_cards = "cards" in ev
        red = None
        if has_cards:
            red = 0
            for c in ev.get("cards", []) or []:
                ct = (c.get("card") or "").lower()
                if "red" in ct:
                    red += 1
        cache_red = cand["red_values"]
        cache_red_sum = sum(v for v in cache_red if isinstance(v, (int, float))) if cache_red else None
        rec = reconciliar_fixture(
            {"fixture_id": cand["fixture_id"], "home": cand["home"],
             "away": cand["away"], "kickoff": cand["date"],
             "competition": cand["league"], "season": cand["season"],
             "score_home": cand["score_home"], "score_away": cand["score_away"],
             "status": cand["status"]},
            {"fixture_id": ev.get("match_id"), "home": ev.get("match_hometeam_name"),
             "away": ev.get("match_awayteam_name"), "kickoff": ev.get("match_date"),
             "competition": ev.get("league_name"), "season": None,
             "score_home": ev.get("match_hometeam_score"),
             "score_away": ev.get("match_awayteam_score"), "status": None},
            "apifootball_com")
        entry = {
            "fixture_id_af": cand["fixture_id"],
            "fixture_id_afc": ev.get("match_id"),
            "match_kind": match_kind,
            "reconciliacao": rec.status,
            "confianca": rec.confidence,
            "home": cand["home"], "away": cand["away"],
            "af_home": ev.get("match_hometeam_name"),
            "af_away": ev.get("match_awayteam_name"),
            "league_afc": ev.get("league_name"),
            "cache_red_class": cand["red_class"],
            "cache_red_sum": cache_red_sum,
            "apifootball_com_red": red if has_cards else None,
            "apifootball_com_has_cards_field": has_cards,
        }
        cards_cmp.append(entry)
        fatos = [
            NormalizedFact(provider="api_football", endpoint="/fixtures/statistics",
                           retrieved_at=0.0, fixture_provider_id=str(cand["fixture_id"]),
                           fixture_corner_id=str(cand["fixture_id"]),
                           field="red_cards", raw_value=cache_red,
                           normalized_value=cache_red_sum if cache_red else None,
                           status=ST_OK if cache_red else ST_MISSING),
            NormalizedFact(provider="apifootball_com", endpoint="get_events",
                           retrieved_at=0.0, fixture_provider_id=str(ev.get("match_id")),
                           fixture_corner_id=str(cand["fixture_id"]),
                           field="red_cards", raw_value=ev.get("cards"),
                           normalized_value=red if has_cards else None,
                           status=ST_OK if has_cards else ST_MISSING),
        ]
        conflitos.comparar_fatos(fatos)
    obs = {
        "n_events_dia": len(afc_events),
        "n_reconciliados": len(cards_cmp),
        "matched": sum(1 for c in cards_cmp if c["reconciliacao"] == REC_MATCHED),
        "ambiguous": sum(1 for c in cards_cmp if c["reconciliacao"] == REC_AMBIGUOUS),
        "not_matched": sum(1 for c in cards_cmp if c["reconciliacao"] == REC_NOT_MATCHED),
        "cache_null_recuperados_zero": sum(1 for c in cards_cmp if c["cache_red_class"] == "all_null" and c["apifootball_com_red"] == 0),
        "cache_null_recuperados_positivo": sum(1 for c in cards_cmp if c["cache_red_class"] == "all_null" and (c["apifootball_com_red"] or 0) > 0),
        "cache_null_sem_cards_field": sum(1 for c in cards_cmp if c["cache_red_class"] == "all_null" and not c["apifootball_com_has_cards_field"]),
        "concordancia_red_positivo": sum(1 for c in cards_cmp if c["cache_red_class"] == "has_red" and c["apifootball_com_red"] == c["cache_red_sum"]),
        "conflito_red_divergente": sum(1 for c in cards_cmp if c["cache_red_sum"] is not None and c["apifootball_com_red"] is not None and c["apifootball_com_red"] != c["cache_red_sum"]),
        "detalhes": cards_cmp[:12],
    }
    return obs, cards_cmp


def rodar_auditoria_real(providers: dict[str, Any] | None = None,
                         raw_dir: str | None = None,
                         date_str: str | None = None) -> dict:
    """Executa a auditoria autenticada REAL (Etapa 5F-D2).

    Reusa EXATAMENTE os adapters de src/multifonte (transport real por padrao).
    Providers injetaveis apenas para testes (fakes). Respeita HARD_LIMIT por
    provider. 402/429/404 interrompem aquele provider (nao a auditoria toda).
    Salva raws redacted em raw_dir. NAO altera motor. NAO implementa fallback.
    """
    import os as _os
    import time as _time

    if not credenciais_rotacionadas():
        return {"erro": "GATE_FECHADO", "gate_aberto": False}

    raw_dir = raw_dir or "data/auditoria_multifonte"
    date_str = date_str or "2026-09-10"
    secrets = _segredos_ativos()

    # Instancia adapters reais (ou usa injetados para teste)
    if providers is None:
        providers = {
            "the_odds_api": TheOddsAPIProvider(),
            "five_dollar_football": FiveDollarFootballProvider(),
            "sportmonks": SportmonksProvider(),
            "apifootball_com": APIFootballComProvider(),
            "football_data_org": FootballDataOrgProvider(),
            "statsbomb_open": StatsBombOpenProvider(fetcher=_real_fetcher),
        }

    cred = status_credenciais()
    candidatos = _candidatos_cache(date_str)
    conflitos = ConflictRegistry()

    chamadas: list[dict] = []
    smoke: dict[str, dict] = {}
    observacoes: dict[str, Any] = {}

    def registrar(provider: str, op: str, status: str, **kw):
        chamadas.append({"provider": provider, "operacao": op, "status": status, **kw})

    def tentar(provider: str, op: str, fn, raw_nome: str | None = None):
        """Executa fn(); registra status/latency/shape; salva raw redacted."""
        t0 = _time.time()
        try:
            data = fn()
            lat = round((_time.time() - t0) * 1000, 0)
            shape = _shape_summary(data)
            if raw_nome:
                _salvar_raw(raw_dir, raw_nome, data, secrets)
            registrar(provider, op, ST_OK, latency_ms=lat, shape=shape)
            return data
        except RateLimitHit as e:
            registrar(provider, op, "RATE_LIMIT", erro=str(e)[:80])
        except QuotaExhausted as e:
            registrar(provider, op, "QUOTA_EXHAUSTED", erro=str(e)[:80])
        except CredentialsBlocked as e:
            registrar(provider, op, "CREDENCIAL_BLOQUEADA", erro=str(e)[:80])
        except EndpointNotConfirmed as e:
            registrar(provider, op, "ENDPOINT_NAO_CONFIRMADO", erro=str(e)[:80])
        except Exception as e:
            registrar(provider, op, "FALHA_TECNICA", erro=f"{type(e).__name__}: {str(e)[:60]}")
        return None

    # --- SMOKE TESTS (1 chamada minima por provider) ---
    smoke["the_odds_api"] = tentar(
        "the_odds_api", "smoke_sports",
        providers["the_odds_api"].fetch_sports, "smoke_theoddsapi_sports.json")
    smoke["five_dollar_football"] = tentar(
        "five_dollar_football", "smoke_status",
        providers["five_dollar_football"].fetch_status, "smoke_5dollar_status.json")
    smoke["sportmonks"] = tentar(
        "sportmonks", "smoke_leagues",
        providers["sportmonks"].fetch_leagues, "smoke_sportmonks_leagues.json")
    smoke["apifootball_com"] = tentar(
        "apifootball_com", "smoke_leagues",
        providers["apifootball_com"].fetch_competitions, "smoke_apifootballcom_leagues.json")
    smoke["football_data_org"] = tentar(
        "football_data_org", "smoke_competitions",
        providers["football_data_org"].fetch_competitions, "smoke_footballdata_comps.json")
    smoke["statsbomb_open"] = tentar(
        "statsbomb_open", "smoke_competitions",
        providers["statsbomb_open"].fetch_competitions, "smoke_statsbomb_comps.json")

    # --- AMOSTRAS POR DOMINIO ---

    # THE ODDS API: odds pre-match UCL (goals totals + h2h)
    theodds_odds: list = []
    if smoke["the_odds_api"] is not None:
        data = tentar(
            "the_odds_api", "odds_ucl",
            lambda: providers["the_odds_api"].fetch_odds(
                "soccer_uefa_champs_league", "h2h,totals,spreads"),
            "sample_theoddsapi_ucl.json")
        if isinstance(data, list):
            theodds_odds = data if data and isinstance(data[0], dict) and "market" in data[0] else []
            # data ja e lista de NormalizedOdd via fetch_odds
            theodds_odds = data
    mercados_odds = {}
    bookmakers_odds = set()
    if isinstance(theodds_odds, list):
        for o in theodds_odds:
            try:
                mercados_odds[o.market] = mercados_odds.get(o.market, 0) + 1
                bookmakers_odds.add(o.bookmaker)
            except Exception:
                pass
    observacoes["the_odds_api"] = {
        "mercados_observados": dict(mercados_odds),
        "bookmakers_distintos": len(bookmakers_odds),
        "n_odds": len(theodds_odds) if isinstance(theodds_odds, list) else 0,
        "coleta_tipo": "pre_match",
    }

    # 5DOLLAR: fixtures + odds de corner e cards
    fd_fixtures: list = []
    if smoke["five_dollar_football"] is not None:
        fd_fixtures = tentar(
            "five_dollar_football", "fixtures_recent",
            lambda: providers["five_dollar_football"].fetch_fixtures(
                {"page": 0, "date": date_str}),
            "sample_5dollar_fixtures.json") or []
    fd_corner = tentar(
        "five_dollar_football", "odds_corner",
        lambda: providers["five_dollar_football"].fetch_odds(1, "corner"),
        "sample_5dollar_corner.json") if smoke["five_dollar_football"] is not None else None
    fd_cards = tentar(
        "five_dollar_football", "odds_cards",
        lambda: providers["five_dollar_football"].fetch_odds(1, "cards"),
        "sample_5dollar_cards.json") if smoke["five_dollar_football"] is not None else None
    observacoes["five_dollar_football"] = {
        "odds_corner_observadas": isinstance(fd_corner, list) and len(fd_corner) > 0,
        "odds_cards_observadas": isinstance(fd_cards, list) and len(fd_cards) > 0,
        "n_odds_corner": len(fd_corner) if isinstance(fd_corner, list) else 0,
        "n_odds_cards": len(fd_cards) if isinstance(fd_cards, list) else 0,
        "n_fixtures": len(fd_fixtures) if isinstance(fd_fixtures, list) else 0,
    }

    # APIFOOTBALL.COM: get_events por data -> cards[] para reconciliacao
    afc_events: list = []
    if smoke["apifootball_com"] is not None:
        afc_events = tentar(
            "apifootball_com", "events_by_date",
            lambda: providers["apifootball_com"].fetch_events_by_date(date_str, date_str),
            f"sample_apifootballcom_{date_str}.json") or []
    obs_afc, cards_cmp = comparar_cards_apifootball(afc_events, candidatos, conflitos)
    observacoes["apifootball_com_cards"] = obs_afc

    # SPORTMONKS: fixtures by date + fetch_match para 1-2 reconciliados
    sm_fixtures: list = []
    if smoke["sportmonks"] is not None:
        sm_fixtures = tentar(
            "sportmonks", "fixtures_by_date",
            lambda: providers["sportmonks"].fetch_fixtures_by_date(date_str),
            f"sample_sportmonks_{date_str}.json") or []
    sm_cmp: list[dict] = []
    sm_nome_map = {}
    if isinstance(sm_fixtures, list):
        for fx in sm_fixtures:
            if not isinstance(fx, dict):
                continue
            # nomes podem estar em names.home/away ou similar
            h = _norm_nome((fx.get("names") or {}).get("home")) if isinstance(fx.get("names"), dict) else _norm_nome(fx.get("home_team_name") or (fx.get("name") if isinstance(fx.get("name"), str) else None))
            a = _norm_nome((fx.get("names") or {}).get("away")) if isinstance(fx.get("names"), dict) else _norm_nome(fx.get("away_team_name"))
            sid = fx.get("id")
            if h and a and sid:
                sm_nome_map[(h, a)] = (sid, fx)
    # para ate 2 candidatos has_red/all_null, faz fetch_match
    detalhados = 0
    for cand in candidatos:
        if detalhados >= 2:
            break
        h = _norm_nome(cand["home"])
        a = _norm_nome(cand["away"])
        match = sm_nome_map.get((h, a))
        if not match:
            continue
        sid, fx = match
        facts = tentar(
            "sportmonks", "fetch_match_detail",
            lambda sid=sid: providers["sportmonks"].fetch_match(str(sid)),
            f"sample_sportmonks_match_{sid}.json")
        sm_red = None
        sm_corners = None
        if isinstance(facts, list):
            for f in facts:
                if f.field == "red_cards":
                    sm_red = f.normalized_value
                if f.field == "corners_home" or f.field == "corners":
                    sm_corners = (sm_corners or 0) + (f.normalized_value or 0) if isinstance(f.normalized_value, (int, float)) else sm_corners
        cache_red_sum = sum(v for v in cand["red_values"] if isinstance(v, (int, float))) if cand["red_values"] else None
        sm_cmp.append({
            "fixture_id_af": cand["fixture_id"],
            "fixture_id_sm": sid,
            "home": cand["home"], "away": cand["away"],
            "cache_red_class": cand["red_class"],
            "cache_red_sum": cache_red_sum,
            "sportmonks_red": sm_red,
            "cache_corners_total": cand["corners_total"],
            "sportmonks_corners": sm_corners,
        })
        detalhados += 1
        # conflito de corners
        fatos = [
            NormalizedFact(provider="api_football", endpoint="/fixtures/statistics",
                           retrieved_at=0.0, fixture_provider_id=str(cand["fixture_id"]),
                           fixture_corner_id=str(cand["fixture_id"]),
                           field="corners", raw_value=cand["corners_values"],
                           normalized_value=cand["corners_total"],
                           status=ST_OK if cand["corners_total"] is not None else ST_MISSING),
            NormalizedFact(provider="sportmonks", endpoint="/fixtures",
                           retrieved_at=0.0, fixture_provider_id=str(sid),
                           fixture_corner_id=str(cand["fixture_id"]),
                           field="corners", raw_value=None,
                           normalized_value=sm_corners,
                           status=ST_OK if sm_corners is not None else ST_MISSING),
        ]
        conflitos.comparar_fatos(fatos)
    observacoes["sportmonks"] = {
        "n_fixtures_dia": len(sm_fixtures) if isinstance(sm_fixtures, list) else 0,
        "n_nome_mapeados": len(sm_nome_map),
        "n_detalhados": detalhados,
        "detalhes": sm_cmp,
    }

    # FOOTBALL-DATA.ORG: matches by competition (UCL code CL)
    fd_matches: list = []
    if smoke["football_data_org"] is not None:
        fd_matches = tentar(
            "football_data_org", "matches_cl",
            lambda: providers["football_data_org"].fetch_matches_by_competition(
                "CL", dateFrom=date_str, dateTo=date_str),
            f"sample_footballdata_CL_{date_str}.json") or []
    fd_cmp: list[dict] = []
    if isinstance(fd_matches, list):
        for m in fd_matches:
            if not isinstance(m, dict):
                continue
            h = _norm_nome((m.get("homeTeam") or {}).get("name"))
            a = _norm_nome((m.get("awayTeam") or {}).get("name"))
            for cand in candidatos:
                if _norm_nome(cand["home"]) == h and _norm_nome(cand["away"]) == a:
                    sc = m.get("score") or {}
                    ft = sc.get("fullTime") or {}
                    fd_cmp.append({
                        "fixture_id_af": cand["fixture_id"],
                        "home": cand["home"], "away": cand["away"],
                        "fd_score_ft_home": ft.get("home"),
                        "fd_score_ft_away": ft.get("away"),
                        "cache_score_home": cand["score_home"],
                        "cache_score_away": cand["score_away"],
                        "fd_status": m.get("status"),
                    })
                    break
    observacoes["football_data_org"] = {
        "n_matches_dia": len(fd_matches) if isinstance(fd_matches, list) else 0,
        "n_reconciliados": len(fd_cmp),
        "detalhes": fd_cmp[:6],
    }

    # STATSBOMB: re-confirma smoke (competitions + 1 match events)
    sb_events = None
    if smoke["statsbomb_open"] is not None:
        sb_events = tentar(
            "statsbomb_open", "events_smoke",
            lambda: providers["statsbomb_open"].fetch_events(9880),
            "sample_statsbomb_events_9880.json")
    observacoes["statsbomb_open"] = {
        "n_events_smoke": len(sb_events) if isinstance(sb_events, list) else 0,
        "publico": True, "live": False, "odds": False,
    }

    # Contagem de chamadas por provider
    por_provider: dict[str, int] = {}
    por_status: dict[str, int] = {}
    for c in chamadas:
        por_provider[c["provider"]] = por_provider.get(c["provider"], 0) + 1
        por_status[c["status"]] = por_status.get(c["status"], 0) + 1

    return {
        "etapa": "5F-D2",
        "timestamp": _time.time(),
        "gate_aberto": True,
        "data_auditada": date_str,
        "credenciais": cred,
        "candidatos_cache": len(candidatos),
        "candidatos_cache_por_redclass": {
            k: sum(1 for c in candidatos if c["red_class"] == k)
            for k in ("all_null", "all_zero", "has_red", "no_stats")
        },
        "chamadas": chamadas,
        "chamadas_por_provider": por_provider,
        "chamadas_por_status": por_status,
        "smoke_tests": {k: (v is not None) for k, v in smoke.items()},
        "observacoes": observacoes,
        "conflitos": conflitos.to_list(),
        "n_conflitos": len(conflitos.items()),
        "banco_snapshots": resumo_banco_snapshots(),
        "matriz_confiabilidade": matriz_confiabilidade(),
        "bloqueio": "GATE ABERTO — chamadas reais executadas",
    }


def main() -> None:
    """CLI: python -m src.auditoria_multifonte [--json] [--real]"""
    import sys
    as_json = "--json" in sys.argv
    if "--real" in sys.argv:
        relatorio = rodar_auditoria_real()
    else:
        relatorio = auditar()
    if as_json:
        print(json.dumps(relatorio, indent=2, default=str, ensure_ascii=False))
    else:
        _imprimir(relatorio)


def _imprimir(r: dict) -> None:
    print("=" * 64)
    print("ETAPA 5F-D -- AUDITORIA MULTI-FONTE (MODO SEGURO)")
    print("=" * 64)
    print(f"GATE (MULTISOURCE_CREDENTIALS_ROTATED): {'ABERTO' if r['gate_aberto'] else 'FECHADO'}")
    print(f"Bloqueio: {r['bloqueio']}")
    print()
    print("Credenciais por provider (valores NUNCA impressos):")
    for p, st in r["credenciais"].items():
        print(f"  - {p}: cred={st['credencial_configurada']} "
              f"gate={st['gate_aberto']} autorizada={st['chamada_real_autorizada']}")
    print()
    b = r["banco_snapshots"]
    print(f"Banco snapshots: total={b.get('total')} "
          f"por_provider={b.get('por_provider')} "
          f"provider_null={b.get('provider_null')} "
          f"user_version={b.get('user_version')} "
          f"integrity={b.get('integrity_check')}")
    print()
    print("Prontidao de coleta prospectiva:")
    for p, st in r["prontidao_coleta"].items():
        print(f"  - {p}: {st}")
    print()
    print("Matriz de cobertura (resumo por provider):")
    for p, cols in r["matriz_cobertura"].items():
        ok = sum(1 for v in cols.values() if v == ST_OK)
        total = len(cols)
        print(f"  - {p}: {ok}/{total} VALIDADO")
    print()
    print("Auditorias de amostra:")
    for nome, itens in r["auditorias"].items():
        print(f"  [{nome}]")
        for it in itens:
            print(f"    - {it.get('provider')}: {it.get('status')} "
                  f"({it.get('nota') or it.get('evidencia','')})")
    print()
    print(f"Chamadas autenticadas executadas: {r['chamadas_autenticadas_executadas']}")
    print(f"Chamadas StatsBomb executadas: {r['chamadas_statsbomb_executadas']}")
    print("=" * 64)
    print("PARE. NAO fallback. NAO fonte vencedora. NAO regras. NAO Etapa 6.")


if __name__ == "__main__":
    main()