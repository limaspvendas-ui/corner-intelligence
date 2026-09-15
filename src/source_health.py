"""Saude das fontes: circuit breaker por provedor + cota da API-Football.

Camada de OPERACAO, totalmente separada do motor de aposta: nao importa
nem altera analysis/prejogo_opportunity/live_opportunity/policy ou
qualquer regra de decisao. Nao calcula nada estatistico.

Principios (failover multifonte):
    - Circuit breaker POR PROVEDOR, estado persistido em SQLite (vale
      entre processos/requests). Estados: FECHADO (saudavel),
      ABERTO (nao consultar ate `aberto_ate`).
    - Gatilhos de abertura: QUOTA_EXHAUSTED (abre ate o reset diario),
      RATE_LIMIT (60s), TIMEOUT (30s apos retry tecnico limitado),
      HTTP 5XX (30s apos retry limitado), CONNECTION_ERROR (30s),
      CREDENCIAL (10 min — exige intervecao humana).
    - NUNCA loop entre provedores: quem chama registra as tentativas.
    - Cota: contadores vem dos headers x-ratelimit-* da resposta real
      (plano PRO: 7.500/dia, 300/min). NUNCA se registra ou imprime
      valor, prefixo, sufixo, tamanho ou hash da API_KEY.
    - "Aberto" NUNCA significa "zero jogos": significa NAO CONSULTAR a
      fonte ate o reset; o chamador reporta NAO DISPONIVEL ou usa
      fallback compativel — nunca converte em resultado vazio.
"""
from __future__ import annotations

import sqlite3
import time
from datetime import datetime, timezone
from typing import Any

from src.config import DATA_DIR, DB_PATH

# Gatilhos (Etapa 6) e cooldowns por motivo (Etapa 7)
GATILHO_QUOTA = "QUOTA_EXHAUSTED"
GATILHO_RATE_LIMIT = "RATE_LIMIT"
GATILHO_TIMEOUT = "TIMEOUT"
GATILHO_5XX = "HTTP_5XX"
GATILHO_CONEXAO = "CONNECTION_ERROR"
GATILHO_CREDENCIAL = "CREDENCIAL"

COOLDOWN_SEG: dict[str, int] = {
    GATILHO_RATE_LIMIT: 60,      # respeitar o reset por minuto
    GATILHO_TIMEOUT: 30,         # retry tecnico limitado + pausa curta
    GATILHO_5XX: 30,             # retry limitado + pausa curta
    GATILHO_CONEXAO: 30,
    GATILHO_CREDENCIAL: 600,     # exige acao humana; nao martelar
    GATILHO_QUOTA: 0,            # especial: ate o reset diario (abaixo)
}

PLANO_API_FOOTBALL = "PRO (7.500 requisicoes/dia; 300/min)"

# ----------------------------------------------------------------------
# Persistencia (tabelas proprias; nao toca api_cache nem tabelas do motor)
# ----------------------------------------------------------------------
_SCHEMA = """
CREATE TABLE IF NOT EXISTS provider_circuit (
    provider   TEXT PRIMARY KEY,
    estado     TEXT NOT NULL,          -- 'FECHADO' | 'ABERTO'
    motivo     TEXT,
    aberto_em  REAL,
    aberto_ate REAL
);
CREATE TABLE IF NOT EXISTS api_football_quota (
    dia               TEXT PRIMARY KEY,   -- YYYY-MM-DD (UTC)
    requests_limit    INTEGER,
    requests_remaining INTEGER,
    atualizado_em     REAL
);
CREATE TABLE IF NOT EXISTS fonte_requisicao_log (
    ts        REAL NOT NULL,
    provider  TEXT NOT NULL,
    endpoint  TEXT NOT NULL,
    resultado TEXT NOT NULL   -- 'OK' | motivo do erro (nunca credenciais)
);
"""


def _connect() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.executescript(_SCHEMA)
    return conn


# ----------------------------------------------------------------------
# Circuit breaker
# ----------------------------------------------------------------------
def circuito_abrir(provider: str, motivo: str, ate_ts: float | None = None) -> None:
    """Abre o circuito do provedor ate `ate_ts` (epoch). QUOTA sem ate_ts:
    abre ate o proximo reset diario (00:00 UTC do dia seguinte + 5 min)."""
    if ate_ts is None:
        if motivo == GATILHO_QUOTA:
            now = datetime.now(timezone.utc)
            amanha = datetime(now.year, now.month, now.day, tzinfo=timezone.utc)
            from datetime import timedelta as _td

            reset = amanha + _td(days=1, minutes=5)
            ate_ts = reset.timestamp()
        else:
            ate_ts = time.time() + COOLDOWN_SEG.get(motivo, 30)
    with _connect() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO provider_circuit "
            "(provider, estado, motivo, aberto_em, aberto_ate) VALUES (?,?,?,?,?)",
            (provider, "ABERTO", motivo, time.time(), ate_ts),
        )


def circuito_fechar(provider: str) -> None:
    """Fecha o circuito (fonte recuperada — ex.: cota renovada)."""
    with _connect() as conn:
        conn.execute(
            "UPDATE provider_circuit SET estado='FECHADO', motivo=NULL, "
            "aberto_em=NULL, aberto_ate=NULL WHERE provider=?",
            (provider,),
        )


def circuito_info(provider: str) -> dict[str, Any]:
    """Estado atual do circuito (dict simples; sem segredos)."""
    with _connect() as conn:
        row = conn.execute(
            "SELECT estado, motivo, aberto_em, aberto_ate FROM provider_circuit "
            "WHERE provider=?",
            (provider,),
        ).fetchone()
    if row is None:
        return {"provider": provider, "estado": "FECHADO", "motivo": None,
                "aberto_ate": None}
    estado, motivo, aberto_em, aberto_ate = row
    if estado == "ABERTO" and aberto_ate is not None and time.time() >= aberto_ate:
        # Cooldown expirado: half-open — permite tentar a fonte primaria de novo
        return {"provider": provider, "estado": "HALF_OPEN", "motivo": motivo,
                "aberto_ate": aberto_ate}
    return {"provider": provider, "estado": estado, "motivo": motivo,
            "aberto_ate": aberto_ate}


def circuito_permite(provider: str) -> bool:
    """True se a fonte pode ser consultada agora (FECHADO ou HALF_OPEN)."""
    info = circuito_info(provider)
    return info["estado"] in ("FECHADO", "HALF_OPEN")


def circuito_resetar(provider: str) -> None:
    """Fecha explicitamente o circuito (uso em testes/manutencao)."""
    circuito_fechar(provider)


# ----------------------------------------------------------------------
# Cota da API-Football (headers reais; NUNCA credenciais)
# ----------------------------------------------------------------------
def registrar_quota_api_football(
    requests_limit: int | None, requests_remaining: int | None,
) -> None:
    """Persiste os contadores do header x-ratelimit-* do dia (UTC)."""
    if requests_limit is None and requests_remaining is None:
        return
    dia = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    with _connect() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO api_football_quota "
            "(dia, requests_limit, requests_remaining, atualizado_em) "
            "VALUES (?,?,?,?)",
            (dia, requests_limit, requests_remaining, time.time()),
        )


def obter_quota_api_football() -> dict[str, Any]:
    """Contadores do dia (UTC). `reset` = proximo 00:00 UTC."""
    dia = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    with _connect() as conn:
        row = conn.execute(
            "SELECT requests_limit, requests_remaining, atualizado_em "
            "FROM api_football_quota WHERE dia=?",
            (dia,),
        ).fetchone()
    if row is None:
        return {"dia_utc": dia, "plano": PLANO_API_FOOTBALL,
                "requests_limit": None, "requests_remaining": None,
                "atualizado_em": None, "reset_utc": f"{dia}T00:00Z (+1 dia)"}
    limit, remaining, atualizado = row
    agora = datetime.now(timezone.utc)
    reset = datetime(agora.year, agora.month, agora.day,
                     tzinfo=timezone.utc)
    from datetime import timedelta as _td

    reset = reset + _td(days=1)
    return {
        "dia_utc": dia,
        "plano": PLANO_API_FOOTBALL,
        "requests_limit": limit,
        "requests_remaining": remaining,
        "atualizado_em": atualizado,
        "reset_utc": reset.strftime("%Y-%m-%dT%H:%MZ"),
    }


# ----------------------------------------------------------------------
# Log de requisicoes externas (contagem por provedor/endpoint — Etapa 11)
# ----------------------------------------------------------------------
def registrar_requisicao(provider: str, endpoint: str, resultado: str) -> None:
    """Registra UMA requisicao externa (contagem de consumo). Sem credenciais."""
    try:
        with _connect() as conn:
            conn.execute(
                "INSERT INTO fonte_requisicao_log (ts, provider, endpoint, resultado) "
                "VALUES (?,?,?,?)",
                (time.time(), provider, endpoint, resultado),
            )
            conn.execute(
                "DELETE FROM fonte_requisicao_log WHERE ts < ?",
                (time.time() - 7 * 24 * 3600,),
            )
    except Exception:
        pass  # log de contagem nunca derruba a coleta


def contar_requisicoes(janela_horas: float = 24.0) -> dict[str, int]:
    """Contagem por provedor na janela (auditoria de consumo)."""
    try:
        with _connect() as conn:
            rows = conn.execute(
                "SELECT provider, COUNT(*) FROM fonte_requisicao_log "
                "WHERE ts >= ? GROUP BY provider",
                (time.time() - janela_horas * 3600,),
            ).fetchall()
        return {p: n for p, n in rows}
    except Exception:
        return {}


# ----------------------------------------------------------------------
# Status operacional consolidado (Etapa 11) — sem segredos
# ----------------------------------------------------------------------
def status_operacional_fontes() -> dict[str, Any]:
    """Bloco de status para /api/status: cota, circuit breaker, consumo.

    NUNCA inclui valor/prefixo/sufixo/tamanho/hash de credencial — apenas
    booleanos de configuracao (reusa a funcao segura do multifonte).
    """
    from src.multifonte import status_credenciais

    circuitos = {
        "api_football": circuito_info("api_football"),
        "football_data_org": circuito_info("football_data_org"),
        "apifootball_com": circuito_info("apifootball_com"),
        "the_odds_api": circuito_info("the_odds_api"),
        "five_dollar_football": circuito_info("five_dollar_football"),
        "sportmonks": circuito_info("sportmonks"),
        "statsbomb_open": circuito_info("statsbomb_open"),
    }
    cred = status_credenciais()
    return {
        "fonte_principal": "API-Football v3 (api-sports.io)",
        "plano": PLANO_API_FOOTBALL,
        "quota_api_football": obter_quota_api_football(),
        "circuit_breakers": circuitos,
        "credenciais_configuradas": {
            p: v["credencial_configurada"] for p, v in cred.items()
        },
        "gate_multifonte_aberto": cred.get("api_football", {}).get(
            "gate_aberto", False),
        "consumo_requisicoes_24h": contar_requisicoes(24),
    }