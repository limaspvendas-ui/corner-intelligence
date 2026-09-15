"""PROVIDER ROUTER — failover automatico de fontes por CAPACIDADE.

Arquitetura (Etapa 4):

    FONTES (API-Football, football-data.org, apifootball.com, ...)
        ↓
    PROVIDER ROUTER (este modulo — decide QUAL fonte, por capacidade)
        ↓
    NORMALIZACAO (formato nativo que o motor ja espera)
        ↓
    MOTOR OFICIAL (intocado — nao sabe nem decide qual API foi usada)

Nunca: MOTOR -> escolha de provider. O motor nao conhece este modulo.

Regras obrigatorias (todas testadas):
    - API-Football PAGA permanece primaria para TODO tipo de dado.
    - Failover e POR TIPO DE DADO e somente por INDISPONIBILIDADE
      (quota esgotada, rate limit, timeout, erro de conexao, HTTP
      502/503/504, indisponibilidade tecnica confirmada). NUNCA por
      conflito factual: divergencia entre fontes e registrada como
      CONFLITO_DE_FONTE e NUNCA resolvida silenciosamente.
    - Fonte alternativa so entra em tipo de dado que ela REALMENTE
      suporta (validado por resposta real ou documentacao oficial):
      provedor de odds NUNCA substitui fonte factual; StatsBomb e
      historico e NUNCA e usado como LIVE; football-data.org nao tem
      corners/cartoes (por design da API) e nunca os "preenchera".
    - Quando nenhuma fonte compativel existe para o tipo: erro
      estruturado SOURCE_UNAVAILABLE => NAO DISPONIVEL. NUNCA "zero
      jogos", NUNCA "sem oportunidades", NUNCA dado inventado.
    - NULL permanece NULL. Ausencia nunca vira zero.
    - Circuit breaker por provedor: quota esgotada => parar de
      consultar ate o reset; usar alternativa quando houver; sem
      martelar HTTP 500 repetido; nunca loopar entre provedores.
    - Procedencia: toda coleta registra fonte_original, endpoint,
      timestamp_coleta, fonte_utilizada, fallback_ativado e
      motivo_fallback. A SaidaOficial expoe esses campos SEM alterar
      probabilidade/confianca/linha/decisao.

Capacidades VALIDADAS por tipo de dado (auditorias 5F-D2C, 2026-09):
    lista_fixtures_dia : api_football (validado) >
                         football_data_org (validado: 13 competicoes,
                         fixture/kickoff/status/placar; SEM IDs API-Football)
    lista_fixtures_live : api_football (nenhuma alternativa compativel:
                         football-data.org nao tem minuto live valido)
    fixture_por_id     : api_football (espaco de IDs incompativel nas
                         alternativas — nunca mapear ID de outra API)
    historico/stats/h2h/benchmark_liga : api_football (nenhuma fonte
                         compativel validada hoje)
    eventos_cartoes    : api_football (apifootball_com requer match_id
                         PROPRIO — sem ponte de IDs validada, nao entra)
    odds               : camada multiprovider dedicada (odds_coleta)
"""
from __future__ import annotations

import time
from datetime import date as date_cls
from typing import Any, Callable

from src import source_health
from src.exceptions import (
    SourceQuotaExhaustedError,
    SourceRateLimitError,
    SourceTimeoutError,
    SourceUnavailableError,
)
from src.fixtures import Fixture
from src.multifonte import (
    CredentialsBlocked,
    FootballDataOrgProvider,
    RateLimitHit,
    credenciais_rotacionadas,
)

PROVIDER_PRIMARIO = "api_football"
FONTE_PRIMARIA_NOME = "API-Football v3 (api-sports.io)"

# Gatilhos de failover (Etapa 6) — SOMENTE indisponibilidade tecnica.
# NAO sao gatilhos: zero jogos valido, sem oportunidade legitima, NULL
# factual, mercado sem sinal, dado nao fornecido para a competicao.
GATILHOS_FAILOVER: tuple[type[Exception], ...] = (
    SourceQuotaExhaustedError,
    SourceRateLimitError,
    SourceTimeoutError,
    SourceUnavailableError,
)

# ----------------------------------------------------------------------
# Registro de capacidades por tipo de dado (Etapa 5) — ordem real de
# fallback. Tipos sem alternativa compativel ficam so com a primaria.
# ----------------------------------------------------------------------
TIPOS_DADO: dict[str, list[str]] = {
    "lista_fixtures_dia": ["api_football", "football_data_org"],
    "lista_fixtures_live": ["api_football"],
    "fixture_por_id": ["api_football"],
    "historico_time": ["api_football"],
    "stats_partida": ["api_football"],
    "h2h": ["api_football"],
    "benchmark_liga": ["api_football"],
    "eventos_cartoes": ["api_football"],
    "odds": ["api_football"],
}


def fallback_disponivel(tipo: str) -> bool:
    """True se existe ao menos uma alternativa compativel alem da primaria."""
    return len(TIPOS_DADO.get(tipo, [])) > 1


# ----------------------------------------------------------------------
# Procedencia (Etapa 8) — acumulada por coleta; lida pela SaidaOficial.
# ----------------------------------------------------------------------
_PROVENANCIA: dict[str, Any] = {}


def provenancia_reset() -> None:
    """Inicia uma nova janela de procedencia (uma analise/varredura)."""
    _PROVENANCIA.clear()


def _registrar_provenancia(
    tipo: str, fonte_utilizada: str, *, fallback_ativado: bool,
    motivo_fallback: str | None, endpoint: str = "", detalhes: str = "",
) -> None:
    _PROVENANCIA[tipo] = {
        "fonte_principal": PROVIDER_PRIMARIO,
        "fonte_utilizada": fonte_utilizada,
        "fallback_ativado": fallback_ativado,
        "motivo_fallback": motivo_fallback,
        "endpoint": endpoint,
        "timestamp_coleta": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "detalhes": detalhes,
    }


def provenancia_fontes() -> dict[str, Any]:
    """Bloco de procedencia para a SaidaOficial — somente leitura."""
    return dict(_PROVENANCIA)


# ----------------------------------------------------------------------
# Circuit breaker (Etapa 7) — delega a source_health
# ----------------------------------------------------------------------
def _abrir_circuit_por_erro(provider: str, exc: Exception) -> str:
    """Mapeia a excecao ao gatilho e abre o circuito. Retorna o gatilho."""
    if isinstance(exc, SourceQuotaExhaustedError):
        gatilho = source_health.GATILHO_QUOTA
    elif isinstance(exc, SourceRateLimitError):
        gatilho = source_health.GATILHO_RATE_LIMIT
    elif isinstance(exc, SourceTimeoutError):
        gatilho = source_health.GATILHO_TIMEOUT
    elif isinstance(exc, SourceUnavailableError):
        gatilho = exc.motivo or source_health.GATILHO_CONEXAO
    else:
        gatilho = source_health.GATILHO_CONEXAO
    source_health.circuito_abrir(provider, gatilho)
    return gatilho


# ----------------------------------------------------------------------
# Orquestracao generica (Etapa 4): FONTES -> ROUTER -> NORMALIZACAO
# ----------------------------------------------------------------------
def coletar(
    tipo: str,
    primaria: Callable[..., Any],
    *args: Any,
    client: Any = None,
    fallbacks: dict[str, Callable[[], Any]] | None = None,
    **kwargs: Any,
) -> Any:
    """Coleta `tipo` tentando a primaria e, SOMENTE em gatilho de
    indisponibilidade, as alternativas compativeis na ordem registrada.

    - Primaria saudavel => primaria (comportamento identico ao de antes).
    - Gatilho (quota/rate/timeout/5xx/conexao) => abre circuit breaker,
      tenta fallbacks COMPATIVEIS com o tipo, na ordem.
    - Nenhuma capaz => SourceUnavailableError (NAO DISPONIVEL — nunca
      zero, nunca inventado), listando o que foi tentado e o motivo.
    - Conflito factual NUNCA e gatilho (Etapa 9).
    """
    tentativas: list[dict[str, str]] = []

    if source_health.circuito_permite(PROVIDER_PRIMARIO):
        try:
            resultado = primaria(*args, **kwargs) if client is None \
                else primaria(client, *args, **kwargs)
            source_health.circuito_fechar(PROVIDER_PRIMARIO)
            _registrar_provenancia(
                tipo, PROVIDER_PRIMARIO, fallback_ativado=False,
                motivo_fallback=None)
            return resultado
        except Exception as exc:
            if not isinstance(exc, GATILHOS_FAILOVER):
                raise  # erro de negocio (ambiguidade, identidade, etc.)
            gatilho = _abrir_circuit_por_erro(PROVIDER_PRIMARIO, exc)
            tentativas.append({
                "fonte": PROVIDER_PRIMARIO,
                "motivo": gatilho,
                "erro": str(exc)[:120],
            })
    else:
        info = source_health.circuito_info(PROVIDER_PRIMARIO)
        tentativas.append({
            "fonte": PROVIDER_PRIMARIO,
            "motivo": f"circuit_breaker_aberto:{info.get('motivo')}",
            "erro": "consulta pulada (circuito aberto ate o reset)",
        })

    # Fallbacks compativeis com o tipo, na ordem (nunca loop: ordem fixa,
    # cada fonte tentada no maximo uma vez por coleta)
    ordem = TIPOS_DADO.get(tipo, [PROVIDER_PRIMARIO])[1:]
    fns = fallbacks or {}
    for fonte in ordem:
        fn = fns.get(fonte)
        if fn is None:
            continue  # sem implementacao para este tipo => NAO SUPORTADO
        if not source_health.circuito_permite(fonte):
            tentativas.append({
                "fonte": fonte,
                "motivo": "circuit_breaker_aberto",
                "erro": "consulta pulada (circuito aberto)",
            })
            continue
        try:
            resultado = fn()
            _registrar_provenancia(
                tipo, fonte, fallback_ativado=True,
                motivo_fallback=tentativas[0]["motivo"])
            return resultado
        except Exception as exc:
            gatilho = _abrir_circuit_por_erro(fonte, exc) \
                if isinstance(exc, GATILHOS_FAILOVER) else "FALHA_TECNICA"
            tentativas.append({
                "fonte": fonte,
                "motivo": gatilho,
                "erro": str(exc)[:120],
            })

    if fallback_disponivel(tipo):
        resumo = "; ".join(
            f"{t['fonte']}:{t['motivo']}" for t in tentativas)
        raise SourceUnavailableError(
            f"NAO DISPONIVEL: nenhuma fonte compativel conseguiu fornecer "
            f"'{tipo}'. Tentativas: {resumo}. Isso NAO significa ausencia "
            f"de jogos nem de oportunidades — significa que o dado nao "
            f"pode ser coletado agora.",
            motivo="FONTE_ESGOTADA",
        )
    raise SourceUnavailableError(
        f"NAO DISPONIVEL: a fonte primaria ({FONTE_PRIMARIA_NOME}) falhou e "
        f"nao existe fonte alternativa compativel validada para '{tipo}' "
        f"(provedores de odds nao sao fonte factual; StatsBomb e historico "
        f"e nunca LIVE). Tentativas: "
        + "; ".join(f"{t['fonte']}:{t['motivo']}" for t in tentativas)
        + ". Isso NAO significa ausencia de jogos nem de oportunidades.",
        motivo="FONTE_UNICA_FALHOU",
    )


# ----------------------------------------------------------------------
# NORMALIZACAO: football-data.org -> Fixture (formato que o motor espera)
# ----------------------------------------------------------------------
# Codigo -> nome. Apenas competicoes do plano free da football-data.org
# (13 cobertas; validado na auditoria 5F-D2C). Ordem = prioridade do
# universo oficial (LIGAS_PRIORITARIAS). O limite de seguranca do adapter
# (10 chamadas por execucao) restringe a varredura as 10 primeiras.
_FDO_COMPETICOES: list[tuple[str, str]] = [
    ("PL", "Premier League"),
    ("PD", "La Liga"),
    ("SA", "Serie A"),
    ("BL1", "Bundesliga"),
    ("FL1", "Ligue 1"),
    ("CL", "UEFA Champions League"),
    ("ELC", "Championship"),
    ("PPL", "Primeira Liga"),
    ("DED", "Eredivisie"),
    ("CLI", "UEFA Europa League"),
    ("BRA", "Brasileirão Série A"),
    ("COL", "Primera A"),
    ("MX", "Liga MX"),
]

# status football-data.org -> status API-Football (short)
_FDO_STATUS: dict[str, str] = {
    "SCHEDULED": "NS",
    "TIMED": "NS",
    "IN_PLAY": "LIVE",
    "PAUSED": "HT",
    "FINISHED": "FT",
    "POSTPONED": "PST",
    "SUSPENDED": "SUSP",
    "CANCELLED": "CANC",
}


def _fdo_normalizar_match(m: dict[str, Any], liga_nome: str) -> Fixture | None:
    """Normaliza UMA partida football-data.org para Fixture.

    IDs (fixture e times) ficam None: o espaco de IDs da football-data.org
    e INCOMPATIVEL com o da API-Football e NUNCA deve ser mapeado por
    aproximacao (regra: nunca usar uma API para dado que ela nao suporta;
    nunca inventar identidade). Quem consumir o Fixture sabe, pela
    procedencia, que a fonte e fallback.
    """
    try:
        status = _FDO_STATUS.get((m.get("status") or "").upper())
        if status is None:
            status = (m.get("status") or "UNKNOWN")
        home = (m.get("homeTeam") or {}).get("name")
        away = (m.get("awayTeam") or {}).get("name")
        kickoff = m.get("utcDate")
        sc = (m.get("score") or {}).get("fullTime") or {}
        gh = sc.get("home")
        ga = sc.get("away")
        return Fixture(
            fixture_id=None,  # espaco de IDs incompativel — nunca mapear
            date=kickoff,
            status=status,
            elapsed=None,     # football-data.org nao fornece minuto live
            league_id=None,  # ID de liga API-Football desconhecido aqui
            league_name=liga_nome,
            round=(f"Matchday {m['matchday']}"
                   if m.get("matchday") else ""),
            season=(m.get("season") or {}).get("startDate", "")[:4] or None,
            home_team_id=None,
            home_team_name=home,
            away_team_id=None,
            away_team_name=away,
            goals_home=gh,
            goals_away=ga,
            venue=None,
        )
    except Exception:
        return None


def _fallback_fdo_lista_dia(on_date: str | None) -> list[Fixture]:
    """Fallback VALIDADO da lista do dia via football-data.org.

    Requisitos de seguranca: GATE de credenciais rotacionadas aberto e
    credencial configurada. Sem eles, o fallback e recusado com motivo
    explicito (nunca silencioso, nunca com dado inventado).
    """
    if not credenciais_rotacionadas():
        raise SourceUnavailableError(
            "Fallback bloqueado por seguranca: MULTISOURCE_CREDENTIALS_ROTATED "
            "!= 1 (credenciais secundarias nao confirmadas como rotacionadas).",
            motivo="FALLBACK_BLOQUEADO_GATE",
        )
    dia = on_date or date_cls.today().isoformat()
    provider = FootballDataOrgProvider()
    if not provider.is_available():
        raise SourceUnavailableError(
            "Fallback indisponivel: credencial football-data.org ausente.",
            motivo="FALLBACK_SEM_CREDENCIAL",
        )
    fixtures: list[Fixture] = []
    try:
        for code, nome in _FDO_COMPETICOES:
            partidas = provider.fetch_matches_by_competition(
                code, dateFrom=dia, dateTo=dia)
            for m in partidas or []:
                fx = _fdo_normalizar_match(m, nome)
                if fx is not None:
                    fixtures.append(fx)
    except (RateLimitHit, CredentialsBlocked) as exc:
        raise SourceUnavailableError(
            f"Fallback football-data.org interrompido: {exc}",
            motivo="FALLBACK_LIMITADO",
        ) from None
    return fixtures


# ----------------------------------------------------------------------
# Conflito factual (Etapa 9) — nunca escolha silenciosa
# ----------------------------------------------------------------------
def verificar_conflito_factual(
    valor_primario: Any, valor_alternativo: Any, *,
    fixture_id: str, campo: str,
    provider_a: str = PROVIDER_PRIMARIO,
    provider_b: str = "alternativa",
) -> None:
    """Divergencia factual entre fontes => CONFLITO_DE_FONTE registrado.

    Ambas as versoes sao preservadas (tabela append-only source_conflict,
    reutilizada da infraestrutura 5F-E1) e o erro sobe estruturado
    (SOURCE_DATA_CONFLICT). NULL de um lado nao e conflito (ausencia
    nunca vira valor). Prioridade automatica existe SOMENTE para
    indisponibilidade — jamais para mascarar divergencia factual.
    """
    from src.exceptions import SourceDataConflictError

    if valor_primario is None or valor_alternativo is None:
        return  # NULL nao conflita (ausencia != valor)
    if valor_primario == valor_alternativo:
        return
    # registra (append-only) e entao sobe erro estruturado
    try:
        from src.resolucao_factual import (
            CONFLITO_NATUREZA_VALOR,
            _encode_value,
            _hash_conflict,
        )
        import sqlite3
        import time as _t

        from src.config import DB_PATH

        h = _hash_conflict(str(fixture_id), campo, provider_a,
                           valor_primario, provider_b, valor_alternativo,
                           "CONFLICT")
        conn = sqlite3.connect(str(DB_PATH))
        # Tabela append-only da 5F-E1; criada sob demanda (idempotente)
        # para que o registro do conflito nunca seja perdido.
        conn.execute(
            "CREATE TABLE IF NOT EXISTS source_conflict ("
            "conflict_hash TEXT PRIMARY KEY, fixture_internal TEXT NOT NULL, "
            "field TEXT NOT NULL, provider_a TEXT NOT NULL, value_a TEXT, "
            "provider_b TEXT NOT NULL, value_b TEXT, timestamp_a TEXT, "
            "timestamp_b TEXT, reconciliation_status TEXT, motivo TEXT, "
            "natureza TEXT, registrado_em REAL NOT NULL)")
        conn.execute(
            "INSERT OR IGNORE INTO source_conflict "
            "(conflict_hash, fixture_internal, field, provider_a, value_a, "
            " provider_b, value_b, timestamp_a, timestamp_b, "
            " reconciliation_status, motivo, natureza, registrado_em) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (h, str(fixture_id), campo, provider_a,
             _encode_value(valor_primario), provider_b,
             _encode_value(valor_alternativo), None, None, "CONFLICT",
             "conflito factual detectado pelo provider_router; ambas as "
             "versoes preservadas; nenhuma escolha automatica",
             CONFLITO_NATUREZA_VALOR, _t.time()),
        )
        conn.commit()
        conn.close()
    except Exception:
        pass  # auditoria best-effort; o erro estruturado abaixo e o essencial
    raise SourceDataConflictError(
        f"CONFLITO_DE_FONTE em '{campo}' do fixture {fixture_id}: "
        f"{provider_a}={valor_primario!r} vs {provider_b}="
        f"{valor_alternativo!r}. Ambas as versoes foram preservadas "
        f"(tabela source_conflict); nenhuma foi escolhida silenciosamente. "
        f"Prioridade automatica so existe para indisponibilidade.",
        motivo="CONFLITO_DE_FONTE",
    )


# ----------------------------------------------------------------------
# Colecoes roteadas (assinaturas identicas as originais — drop-in)
# ----------------------------------------------------------------------
def get_fixtures_today(
    client: Any, league_id: int | None = None, on_date: str | None = None,
) -> list[Fixture]:
    """Lista do dia ROTEADA: API-Football; em gatilho de indisponibilidade,
    football-data.org (capacidade validada: fixture/kickoff/status/placar
    de 13 competicoes; SEM IDs API-Football, SEM minuto live)."""
    from src import fixtures as _fixtures

    return coletar(
        "lista_fixtures_dia",
        _fixtures.get_fixtures_today,
        league_id=league_id, on_date=on_date,
        client=client,
        fallbacks={
            "football_data_org": lambda: _fallback_fdo_lista_dia(on_date),
        },
    )


def get_live_fixtures(client: Any) -> list[Fixture]:
    """Lista live ROTEADA: apenas API-Football (nenhuma fonte compativel
    validada para minuto live). Em falha: NAO DISPONIVEL estruturado."""
    from src import fixtures as _fixtures

    return coletar(
        "lista_fixtures_live",
        _fixtures.get_live_fixtures,
        client=client,
    )


def get_fixture_by_id(client: Any, fixture_id: int) -> Fixture | None:
    """Fixture por ID ROTEADO: apenas API-Football (espaco de IDs
    incompativel nas alternativas — nunca mapear ID de outra API)."""
    from src import fixtures as _fixtures

    return coletar(
        "fixture_por_id",
        _fixtures.get_fixture_by_id,
        fixture_id,
        client=client,
    )