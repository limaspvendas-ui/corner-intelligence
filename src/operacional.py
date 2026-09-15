"""CAMADA OPERACIONAL OFICIAL do Corner Intelligence.

Fechamento operacional: expoe UMA saída oficial canônica que consome o motor
existente (src/prejogo_opportunity.py, src/policy.py, src/cobertura.py) SEM
duplicar nenhuma lógica matemática (Poisson, thresholds, confianca, settlement,
calibração). A IA (Claude Code, API, MCP, ChatGPT futuramente) consulta esta
saída; a IA NÃO substitui o motor -- interpreta o resultado oficial.

PRINCÍPIOS INEGOCIÁVEIS (fechamento operacional):
  - UM motor. Esta camada consome a saída já existente; não recalcula
    prob/confianca/linha/edge. Os valores exibidos vêm direto do motor.
  - Ação operacional somente onde JÁ existe aprovação estatística OU onde o
    operador decidiu explicitamente habilitar por OVERRIDE (auditável, separado
    do status estatístico). Hoje: GOALS (estatístico) e CORNERS (override).
  - Mercados não aprovados são expostos para OBSERVAÇÃO ou BLOQUEADOS, nunca
    como oportunidade operacional aprovada estatisticamente. Nunca promover
    por cobertura. Override NÃO é aprovação estatística.
  - NULL != ZERO. Ausência nunca vira zero. Dado insuficiente => NÃO AVALIÁVEL.
  - Nenhuma aposta forçada. "NENHUMA OPORTUNIDADE OPERACIONAL APROVADA" é
    resultado válido.
  - Aprovação estatística (GOALS) é SEPARADA de validação econômica/ROI
    (ainda BLOQUEADA). Disponibilidade de odd NÃO vira requisito novo do
    motor estatístico de GOALS nesta etapa.
  - MESMO fixture + MESMOS dados + MESMA versão do motor = MESMA decisão
    oficial. Esta camada é função pura da saída do motor (sem aleatoriedade).
  - Coleta prospectiva (odds, live snapshots, reconciliação, fallback) continua
    existindo em paralelo (src/odds_coleta.py, src/live_pressure.py,
    src/resolucao_factual.py) e não bloqueia este fechamento.

Saída canônica: SaidaOficial (dataclass). Consumível por CLI (src/app.py:
comandos `analisar` e `varredura`), API, MCP e futuramente ChatGPT.
"""
from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Any

from src.fixtures import get_fixtures_today
from src.live import now_brt
from src.policy import jogo_elegivel
from src.prejogo_opportunity import (
    VERSAO_PREJOGO_OP,
    VarreduraPreJogo,
    scan_pregame_opportunities,
)
from src.validacao_multifonte import (
    APROVADO_PROX, EM_OBS, BLOQUEADO, NAO_AVAL,
)

# Versão da camada operacional (NÃO é versão do motor; é versão do gate/
# formato de saída). O motor continua em VERSAO_PREJOGO_OP.
# operacional-1.1-override: habilitação operacional de CORNERS por override
# explícito do operador, preservando o status estatístico real (EM_OBSERVAÇÃO).
VERSAO_CAMADA_OPERACIONAL = "operacional-1.1-override"

# Origem da habilitação operacional (distingue aprovação estatística de
# override do operador -- nunca chamar override de aprovação estatística).
ORIGEM_ESTATISTICO = "estatistico"
ORIGEM_OVERRIDE = "override_usuario"
STATUS_OP_ESTATISTICO = "HABILITADO_ESTATISTICAMENTE"
STATUS_OP_OVERRIDE = "HABILITADO_POR_OVERRIDE_DO_USUARIO"

# ----------------------------------------------------------------------
# 3. REGISTRO OFICIAL DE STATUS DOS MERCADOS
# ----------------------------------------------------------------------
# Fonte: validacao estatistica multifonte (macroetapa 5F-F, baseline
# bt-20260913224716-PRE_GAME). Informativo/operacional -- NÃO é recalibração.
# Nunca promover automaticamente mercado por cobertura.
STATUS_MERCADOS: dict[str, dict[str, str]] = {
    "gols": {
        "status": APROVADO_PROX,
        "motivo": (
            "validado estatisticamente (OOS hit 0.9421, walk-forward estavel "
            "0.93-0.95); unico mercado aprovado para a proxima fase"
        ),
    },
    "resultado": {
        "status": EM_OBS,
        "motivo": (
            "experimental; OOS 0.9259 mas walk-forward variavel "
            "(0.88-0.98); aguarda validacao estatistica"
        ),
    },
    "escanteios": {
        "status": EM_OBS,
        "motivo": (
            "drift temporal confirmado (hit 0.927 -> 0.875 pos-cutoff); "
            "sem fonte alternativa historica para isolar causa"
        ),
    },
    "cartoes": {
        "status": NAO_AVAL,
        "motivo": (
            "limitacao estrutural da fonte (red_cards NULL 5D); "
            "amostra insuficiente (207/207 NA); sem reclassificacao"
        ),
    },
    "pressao_live": {
        "status": BLOQUEADO,
        "motivo": (
            "serie temporal por fixture (marcos 1/15/30/45/60/75/90) "
            "incompleta; experimental a calibrar; sem thresholds operacionais"
        ),
    },
    "odds_roi": {
        "status": BLOQUEADO,
        "motivo": (
            "odds prospectivas nao sobrepoem settled backtest; ROI nao "
            "calculavel; validacao economica pendente"
        ),
    },
}

# Mercados avaliados pelo motor pre-jogo (scan_pregame_opportunities) que
# esta camada roteia. pressao_live e odds_roi NAO sao pre-jogo: aparecem
# somente como entradas de status em blocked.
_MERCADOS_PRE_GAME = ("gols", "escanteios", "resultado", "cartoes")

# ----------------------------------------------------------------------
# OVERRIDE OPERACIONAL EXPLÍCITO DO OPERADOR
# ----------------------------------------------------------------------
# Habilita um mercado operacionalmente INDEPENDENTE do status estatístico,
# preservando o status estatístico real em STATUS_MERCADOS (que NÃO é
# alterado). Auditável: timestamp + decisão humana explícita.
#
# NÃO é aprovação estatística. O status_estatistico real permanece
# EM_OBSERVAÇÃO para escanteios (drift não resolvido legitimamente). O
# operador assume a decisão de habilitar operacionalmente mesmo assim.
#
# A oportunidade individual ainda precisa cumprir as regras do motor
# (aprovada_motor=True). Override não força aposta: se o motor retornar
# NÃO ENTRAR, não há oportunidade.
OVERRIDE_OPERACIONAL: dict[str, dict[str, str]] = {
    "escanteios": {
        "status_operacional": STATUS_OP_OVERRIDE,
        "status_estatistico": EM_OBS,  # preservado -- NÃO é aprovação estatística
        "origem": ORIGEM_OVERRIDE,
        "decisao_humana": "true",
        "timestamp_override": "2026-09-14T00:00:00Z",
        "motivo": (
            "drift temporal confirmado (hit 0.927->0.875 pos-cutoff "
            "2026-05-10, -5.2 p.p.); causa = overdispersion pós-drift "
            "(var/mean 1.10->1.37) + mudanca de composicao (Copa 2026); "
            "4 candidatos testados, NENHUM passou nos 8 criterios "
            "estatisticos; operador habilita operacionalmente por decisao "
            "explicita, preservando status estatistico real (EM_OBSERVAÇÃO)"
        ),
    },
}

# ----------------------------------------------------------------------
# OVERRIDE DE MODO TESTE LIVE (auditável) -- habilita o fluxo LIVE
# operacionalmente em MODO TESTE, INDEPENDENTE do status estatístico.
# NÃO é aprovação estatística: pressao_live permanece BLOQUEADO /
# NÃO_VALIDADO em STATUS_MERCADOS (intocado). O override habilita apenas
# a observação/coleta/teste live; NÃO cria sinal -- só aparecem
# oportunidades que o motor live retornar ENTRAR (aprovada_motor=True).
# Auditável: timestamp + decisão humana + motivo + versão do projeto
# (versao_projeto populada em runtime via _projeto_hash() na saída).
# ----------------------------------------------------------------------
VERSAO_LIVE_OP = "live-op-1.0-experimental"           # motor live (src/live_opportunity.py)
VERSAO_CAMADA_LIVE = "operacional-live-0.1-teste"     # camada live (gate/formato)
STATUS_OP_LIVE_TESTE = "HABILITADO_PARA_TESTE_POR_OVERRIDE_DO_USUARIO"
TIMESTAMP_OVERRIDE_LIVE = "2026-09-14T20:30:00Z"
MOTIVO_OVERRIDE_LIVE = "Liberação explícita do operador para coleta e teste live"
# Frescor: espelha src/live_opportunity.AUDIT_FRESHNESS_SEG (single source
# of truth lá; literal aqui para não acoplar import do motor live no topo).
_LIVE_FRESHNESS_SEG = 180
MODO_TESTE_LIVE: dict[str, str] = {
    "modo_teste": "true",
    "status_estatistico": BLOQUEADO,            # pressao_live NÃO_VALIDADO
    "status_operacional": STATUS_OP_LIVE_TESTE,
    "override_operador": "true",
    "decisao_humana": "true",
    "motivo": MOTIVO_OVERRIDE_LIVE,
    "timestamp_override": TIMESTAMP_OVERRIDE_LIVE,
}

# Decisão oficial por status (o gate operacional -- distinto da decisão do
# motor, que e preservada como aprovada_motor).
_DECISAO_OFICIAL_POR_STATUS = {
    APROVADO_PROX: "ENTRAR",
    EM_OBS: "OBSERVACAO",
    NAO_AVAL: "NAO_AVALIAVEL",
    BLOQUEADO: "BLOQUEADO",
}

_RE_LINHA = re.compile(
    r"^(Over|Under)\s+(\d+(?:[.,]\d+)?)\s+(escanteios|gols|cartoes|resultado)\b",
    re.IGNORECASE,
)


def _projeto_hash() -> str | None:
    """Hash do HEAD do git (provenância). None se não for repo git."""
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=5,
        )
        if out.returncode == 0:
            return out.stdout.strip() or None
    except Exception:
        pass
    return None


def _lado_de_linha(linha: str) -> str | None:
    m = _RE_LINHA.match(linha or "")
    return m.group(1) if m else None


# ----------------------------------------------------------------------
# 6 / 8. SAÍDA OFICIAL ÚNICA
# ----------------------------------------------------------------------
@dataclass
class SaidaOficial:
    """Fonte oficial de verdade para uma análise (fixture ou varredura).

    Consumível por CLI, API, MCP e futuramente ChatGPT. Contém versão do
    motor/projeto, timestamp, fixture, status por mercado, oportunidades
    operacionais (apenas GOALS aprovado), observações e bloqueados.
    """

    fixture_id: int | None
    espec: str
    generated_at: str
    engine_version: str
    camada_version: str
    projeto_hash: str | None
    data_status: str
    markets: dict[str, dict[str, Any]] = field(default_factory=dict)
    operational_opportunities: list[dict[str, Any]] = field(default_factory=list)
    observations: list[dict[str, Any]] = field(default_factory=list)
    blocked: list[dict[str, Any]] = field(default_factory=list)
    provenance: dict[str, Any] = field(default_factory=dict)
    nenhum_aprovado: bool = True
    # Extensão LIVE (modo teste) -- aditiva; defaults preservam o pré-live.
    mode: str = "prejogo"           # "prejogo" | "live"
    modo_teste: bool = False        # True somente em saídas live
    live: dict[str, Any] = field(default_factory=dict)  # bloco live

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _entry_opp(
    varredura: VarreduraPreJogo, av: Any, mercado: str,
) -> dict[str, Any]:
    """Constrói uma entrada a partir de uma AvaliacaoPre do motor.
    NÃO altera prob/confianca/linha -- copia direto do motor."""
    fx = varredura.fixture
    status_info = STATUS_MERCADOS.get(mercado, {"status": NAO_AVAL, "motivo": ""})
    override = OVERRIDE_OPERACIONAL.get(mercado)
    # Origem da habilitação operacional: estatística (GOALS) ou override
    # (CORNERS). Override preserva o status_estatistico real.
    if override is not None:
        origem_op = override["origem"]
        status_op = override["status_operacional"]
        decisao_oficial = "ENTRAR"
        timestamp_override = override.get("timestamp_override")
        motivo_override = override.get("motivo")
    else:
        origem_op = ORIGEM_ESTATISTICO
        status_op = STATUS_OP_ESTATISTICO
        decisao_oficial = _DECISAO_OFICIAL_POR_STATUS.get(
            status_info["status"], "BLOQUEADO")
        timestamp_override = None
        motivo_override = None
    return {
        "fixture_id": getattr(fx, "fixture_id", None),
        "espec": varredura.espec,
        "competicao": getattr(fx, "league_name", None),
        "home": getattr(fx, "home_team_name", None),
        "away": getattr(fx, "away_team_name", None),
        "kickoff": getattr(fx, "date", None),
        "mercado": mercado,
        "linha": av.linha,
        "lado": _lado_de_linha(av.linha),
        "prob": av.prob,
        "confianca": av.confianca,
        "aprovada_motor": True,
        "decisao_oficial": decisao_oficial,
        "status_estatistico": status_info["status"],
        "motivo_status": status_info["motivo"],
        # Origem da habilitação operacional (auditável):
        #  - estatistico: GOALS (APROVADO_PARA_PROXIMA_FASE)
        #  - override_usuario: CORNERS (override explícito, status
        #    estatístico real preservado em status_estatistico)
        "origem_operacional": origem_op,
        "status_operacional": status_op,
        "timestamp_override": timestamp_override,
        "motivo_override": motivo_override,
        "prediction_timestamp": None,  # preenchido pelo caller (generated_at)
        "riscos": list(av.riscos),
        "source": f"motor:{VERSAO_PREJOGO_OP}",
    }


def _rota_mercado(mercado: str) -> str:
    """Roteia um mercado para operational / observation / blocked.

    Override operacional explícito (OVERRIDE_OPERACIONAL) tem precedência
    sobre o status estatístico para o GATE operacional -- MAS o status
    estatístico real é preservado na entrada (status_estatistico). Override
    NÃO é aprovação estatística.
    """
    if mercado in OVERRIDE_OPERACIONAL:
        return "operational"
    st = STATUS_MERCADOS.get(mercado, {}).get("status", NAO_AVAL)
    if st == APROVADO_PROX:
        return "operational"
    if st == EM_OBS:
        return "observation"
    return "blocked"


def _construir_saida(
    varredura: VarreduraPreJogo, generated_at: str,
) -> SaidaOficial:
    """Constrói a SaidaOficial a partir da VarreduraPreJogo do motor.
    Função pura da saída do motor -- mesma entrada => mesma saída."""
    fx = varredura.fixture
    # data_status reflete o veredicto do motor sobre o fixture
    if varredura.motivo_sem_jogo and fx is None:
        data_status = "SEM_JOGO"
    elif varredura.motivo_sem_jogo and fx is not None:
        # jogo encerrado/em andamento (motor recusou pre-jogo)
        data_status = "JOGO_NAO_ELEGIVEL_PRE"
    else:
        data_status = "OK"

    # markets: status por mercado + contagens do motor (quando avaliado)
    markets: dict[str, dict[str, Any]] = {}
    for mk, info in STATUS_MERCADOS.items():
        markets[mk] = {
            "status": info["status"],
            "motivo": info["motivo"],
            "avaliacoes_motor": 0,
            "aprovadas_motor": 0,
        }
    # contagens por mercado a partir das avaliacoes do motor
    for av in varredura.avaliacoes:
        mk = av.mercado
        if mk in markets:
            markets[mk]["avaliacoes_motor"] += 1
    aprovadas_por_mercado: dict[str, int] = {}
    for av in varredura.aprovadas:
        aprovadas_por_mercado[av.mercado] = aprovadas_por_mercado.get(av.mercado, 0) + 1
    for mk, n in aprovadas_por_mercado.items():
        if mk in markets:
            markets[mk]["aprovadas_motor"] = n

    operational: list[dict[str, Any]] = []
    observations: list[dict[str, Any]] = []
    blocked: list[dict[str, Any]] = []

    # Avaliações aprovadas pelo motor, roteadas pelo status do mercado.
    # Apenas GOALS (APROVADO_PROX) vira oportunidade operacional.
    for av in varredura.aprovadas:
        rota = _rota_mercado(av.mercado)
        entry = _entry_opp(varredura, av, av.mercado)
        entry["prediction_timestamp"] = generated_at
        if rota == "operational":
            operational.append(entry)
        elif rota == "observation":
            observations.append(entry)
        else:
            blocked.append(entry)

    # Mercados em observação COM sinal do motor mas NÃO aprovados pelo motor:
    # expostos como observação factual (não operacional). Inclui escanteios/
    # resultado com avaliacoes mas sem aprovacao.
    aprovadas_mercados = {av.mercado for av in varredura.aprovadas}
    for av in varredura.avaliacoes:
        if av.mercado in aprovadas_mercados:
            continue  # já roteado acima
        # Não aprovado pelo motor: override NÃO força aposta. Mercado com
        # override mas sem aprovação do motor => observação (status
        # estatístico real). "Se o motor retornar NÃO ENTRAR: não aparece
        # como oportunidade."
        if av.mercado in OVERRIDE_OPERACIONAL:
            rota = "observation"
        else:
            rota = _rota_mercado(av.mercado)
        if rota in ("observation", "blocked"):
            entry = _entry_opp(varredura, av, av.mercado)
            entry["aprovada_motor"] = False
            entry["prediction_timestamp"] = generated_at
            # Não aprovado pelo motor => não é oportunidade operacional,
            # mesmo com override. decisao_oficial reflete observação.
            if av.mercado in OVERRIDE_OPERACIONAL:
                entry["decisao_oficial"] = "OBSERVACAO"
                entry["status_operacional"] = None
                entry["origem_operacional"] = None
            if rota == "observation":
                observations.append(entry)
            else:
                blocked.append(entry)

    # Mercados não-pre-game (pressao_live, odds_roi): sempre blocked, status-only
    for mk in STATUS_MERCADOS:
        if mk in _MERCADOS_PRE_GAME:
            continue
        info = STATUS_MERCADOS[mk]
        blocked.append({
            "fixture_id": getattr(fx, "fixture_id", None),
            "espec": varredura.espec,
            "mercado": mk,
            "status_estatistico": info["status"],
            "motivo_status": info["motivo"],
            "decisao_oficial": _DECISAO_OFICIAL_POR_STATUS[info["status"]],
            "source": "registro_status_mercados",
        })

    nenhum = len(operational) == 0
    return SaidaOficial(
        fixture_id=getattr(fx, "fixture_id", None),
        espec=varredura.espec,
        generated_at=generated_at,
        engine_version=VERSAO_PREJOGO_OP,
        camada_version=VERSAO_CAMADA_OPERACIONAL,
        projeto_hash=_projeto_hash(),
        data_status=data_status,
        markets=markets,
        operational_opportunities=operational,
        observations=observations,
        blocked=blocked,
        provenance={
            "motor": VERSAO_PREJOGO_OP,
            "camada": VERSAO_CAMADA_OPERACIONAL,
            "projeto_hash": _projeto_hash(),
            "generated_at": generated_at,
            "kickoff": getattr(fx, "date", None),
            "fixture_id": getattr(fx, "fixture_id", None),
            "competicao": getattr(fx, "league_name", None),
            "fonte_dados": "API-Football v3 (api-sports.io)",
            "nota": (
                "Decisao oficial = gate operacional sobre a saida do motor. "
                "GOALS (APROVADO_PARA_PROXIMA_FASE) vira oportunidade "
                "operacional por aprovacao ESTATISTICA. CORNERS vira "
                "oportunidade operacional por OVERRIDE explicito do operador "
                "(status estatistico real EM_OBSERVAÇÃO preservado). Demais "
                "mercados: observacao ou bloqueado."
            ),
        },
        nenhum_aprovado=nenhum,
    )


# ----------------------------------------------------------------------
# 4 / 9. CONSULTA POR FIXTURE
# ----------------------------------------------------------------------
def analisar_fixture(
    client: Any, espec: str, registrar: bool = False,
) -> SaidaOficial:
    """Saída oficial canônica de UM fixture ("Time A x Time B").

    Consome scan_pregame_opportunities (motor) com registrar=False (READ:
    não congela recomendação no registro). Inclui resultado (observação,
    sem custo extra de API). NÃO inclui cartoes (NAO_AVALIAVEL; evita
    chamada extra de league_cards_average -- sem valor operacional).

    O motor decide (Poisson, confianca, aprovação). Esta camada apenas
    roteia a saída pelo status estatístico de cada mercado.
    """
    generated_at = now_brt().strftime("%Y-%m-%d %H:%M:%S")
    varredura = scan_pregame_opportunities(
        client, espec, registrar=registrar,
        incluir_resultado=True, incluir_cartoes=False,
    )
    return _construir_saida(varredura, generated_at)


# ----------------------------------------------------------------------
# 10. MODO VARREDURA POR DATA
# ----------------------------------------------------------------------
def varredura_data(
    client: Any, date_str: str | None = None,
) -> dict[str, Any]:
    """Varredura dos jogos elegíveis de uma data.

    Fluxo: buscar fixtures da data -> filtrar elegíveis (policy, não
    encerrado/não ao vivo) -> executar motor oficial -> saída canônica por
    fixture -> agregar oportunidades/observações/bloqueados. Nunca forçar
    aposta. Se nenhum jogo passar: "NENHUMA OPORTUNIDADE OPERACIONAL
    APROVADA" (resultado válido).
    """
    generated_at = now_brt().strftime("%Y-%m-%d %H:%M:%S")
    fixtures = get_fixtures_today(client, on_date=date_str)

    saidas: list[SaidaOficial] = []
    inelegiveis: list[dict[str, Any]] = []
    for fx in fixtures:
        if fx.is_finished or fx.is_live:
            continue
        elegivel, motivo = jogo_elegivel(
            fx.league_id, fx.league_name,
            fx.home_team_name, fx.away_team_name,
        )
        if not elegivel:
            inelegiveis.append({
                "fixture_id": fx.fixture_id,
                "espec": f"{fx.home_team_name} x {fx.away_team_name}",
                "motivo": motivo,
            })
            continue
        espec = f"{fx.home_team_name} x {fx.away_team_name}"
        try:
            saidas.append(analisar_fixture(client, espec, registrar=False))
        except Exception as e:
            inelegiveis.append({
                "fixture_id": fx.fixture_id,
                "espec": espec,
                "motivo": f"erro_motor: {type(e).__name__}: {str(e)[:80]}",
            })

    opps: list[dict[str, Any]] = []
    obs: list[dict[str, Any]] = []
    blk: list[dict[str, Any]] = []
    for s in saidas:
        opps.extend(s.operational_opportunities)
        obs.extend(s.observations)
        blk.extend(s.blocked)

    nenhum = len(opps) == 0
    return {
        "data": date_str or "hoje",
        "generated_at": generated_at,
        "engine_version": VERSAO_PREJOGO_OP,
        "camada_version": VERSAO_CAMADA_OPERACIONAL,
        "projeto_hash": _projeto_hash(),
        "fixtures_considerados": len(fixtures),
        "fixtures_elegiveis": len(saidas),
        "fixtures_inelegiveis": inelegiveis,
        "operational_opportunities": opps,
        "observations": obs,
        "blocked": blk,
        "nenhum_aprovado": nenhum,
        "mensagem_nenhum": (
            "NENHUMA OPORTUNIDADE OPERACIONAL APROVADA" if nenhum else None
        ),
        "provenance": {
            "motor": VERSAO_PREJOGO_OP,
            "camada": VERSAO_CAMADA_OPERACIONAL,
            "projeto_hash": _projeto_hash(),
            "fonte_dados": "API-Football v3 (api-sports.io)",
            "nota": (
                "GOALS (APROVADO_PARA_PROXIMA_FASE) vira oportunidade "
                "operacional por aprovacao ESTATISTICA. CORNERS vira "
                "oportunidade operacional por OVERRIDE explicito do operador "
                "(status estatistico real EM_OBSERVAÇÃO preservado). "
                "Resultado em observacao. Cards/Live/ROI bloqueados ou nao "
                "avaliaveis."
            ),
        },
    }


# ----------------------------------------------------------------------
# Formatadores (saída resumida para CLI/log; a saída canônica é o dict)
# ----------------------------------------------------------------------
def formatar_saida(saida: SaidaOficial) -> str:
    """Resumo textual legível da SaidaOficial (não substitui o dict canônico)."""
    L: list[str] = []
    L.append(f"=== SAÍDA OFICIAL · {saida.engine_version} (camada {saida.camada_version}) ===")
    L.append(f"fixture: {saida.espec} (id={saida.fixture_id}) · data_status={saida.data_status}")
    L.append(f"generated_at: {saida.generated_at} · projeto_hash: {saida.projeto_hash}")
    L.append("")
    L.append("-- status por mercado --")
    for mk, info in saida.markets.items():
        L.append(f"  {mk:14s} {info['status']:24s} av={info['avaliacoes_motor']} aprov_motor={info['aprovadas_motor']}")
    L.append("")
    if saida.operational_opportunities:
        L.append("-- OPORTUNIDADES OPERACIONAIS --")
        for o in saida.operational_opportunities:
            origem = o.get("origem_operacional", "estatistico")
            tag = "OVERRIDE" if origem == "override_usuario" else "ESTATISTICO"
            L.append(f"  [{tag}] {o['mercado']} | {o['linha']} | prob={o['prob']} conf={o['confianca']} | {o['decisao_oficial']} (status_estat={o['status_estatistico']})")
    else:
        L.append("-- OPORTUNIDADES OPERACIONAIS: NENHUMA APROVADA --")
    if saida.observations:
        L.append("")
        L.append("-- OBSERVAÇÕES (EM_OBSERVAÇÃO, não operacional) --")
        for o in saida.observations:
            L.append(f"  {o['mercado']} | {o['linha']} | prob={o['prob']} conf={o['confianca']} | {o['decisao_oficial']}")
    if saida.blocked:
        L.append("")
        L.append("-- BLOQUEADOS / NÃO AVALIÁVEIS --")
        for b in saida.blocked:
            L.append(f"  {b['mercado']:14s} {b['decisao_oficial']} ({b['status_estatistico']})")
    return "\n".join(L)


def formatar_varredura(res: dict[str, Any]) -> str:
    L: list[str] = []
    L.append(f"=== VARREDURA {res['data']} · {res['engine_version']} (camada {res['camada_version']}) ===")
    L.append(f"considerados={res['fixtures_considerados']} elegiveis={res['fixtures_elegiveis']} inelegiveis={len(res['fixtures_inelegiveis'])}")
    L.append("")
    if res["operational_opportunities"]:
        L.append("-- OPORTUNIDADES OPERACIONAIS --")
        for o in res["operational_opportunities"]:
            origem = o.get("origem_operacional", "estatistico")
            tag = "OVERRIDE" if origem == "override_usuario" else "ESTATISTICO"
            L.append(f"  [{tag}] {o['espec']} | {o['mercado']} | {o['linha']} | prob={o['prob']} conf={o['confianca']}")
    else:
        L.append("-- NENHUMA OPORTUNIDADE OPERACIONAL APROVADA --")
    if res["observations"]:
        L.append("")
        L.append(f"-- OBSERVAÇÕES (EM_OBSERVAÇÃO): {len(res['observations'])} --")
    if res["blocked"]:
        L.append(f"-- BLOQUEADOS/NÃO AVALIÁVEIS: {len(res['blocked'])} --")
    return "\n".join(L)


# ----------------------------------------------------------------------
# 11. MODO TESTE LIVE -- Saída oficial canônica live
# ----------------------------------------------------------------------
# Consome o motor live existente (src/live_opportunity.scan_live_opportunities)
# sem duplicar matemática. Override de MODO (MODO_TESTE_LIVE) habilita o fluxo
# live operacionalmente em teste; NÃO cria sinal. pressao_live permanece
# BLOQUEADO. GOALS (estatístico) e CORNERS (override de mercado) roteiam como
# no pré-live. Sem aposta financeira.
def _live_freshness(hora_str: str) -> str:
    """Classifica o frescor da varredura live a partir do timestamp da
    triagem (varredura.hora, formato dd/mm/YYYY HH:MM:SS). 'FRESCO' se o
    delta estiver em [0, _LIVE_FRESHNESS_SEG]; 'STALE' caso contrário ou
    se não for parseable."""
    try:
        t = datetime.strptime(hora_str, "%d/%m/%Y %H:%M:%S")
    except Exception:
        return "STALE"
    now = now_brt()
    if now.tzinfo is not None:
        now = now.replace(tzinfo=None)
    delta = (now - t).total_seconds()
    return "FRESCO" if 0 <= delta <= _LIVE_FRESHNESS_SEG else "STALE"


def _entry_opp_live(candidato: Any, av: Any, mercado: str) -> dict[str, Any]:
    """Constrói uma entrada live a partir de uma Avaliacao do motor live
    (src/live_opportunity.Avaliacao) + Candidato.snapshot. NÃO altera
    prob/confianca/linha -- copia direto do motor live."""
    snap = candidato.snapshot
    status_info = STATUS_MERCADOS.get(mercado, {"status": NAO_AVAL, "motivo": ""})
    override = OVERRIDE_OPERACIONAL.get(mercado)
    if override is not None:
        origem_op = override["origem"]
        status_op = override["status_operacional"]
        decisao_oficial = "ENTRAR"
        timestamp_override = override.get("timestamp_override")
        motivo_override = override.get("motivo")
    else:
        origem_op = ORIGEM_ESTATISTICO
        status_op = STATUS_OP_ESTATISTICO
        decisao_oficial = _DECISAO_OFICIAL_POR_STATUS.get(
            status_info["status"], "BLOQUEADO")
        timestamp_override = None
        motivo_override = None
    odd = getattr(av, "odd", None)
    return {
        "fixture_id": av.fixture_id,
        "espec": av.jogo,
        "competicao": av.competicao,
        "home": snap.home_team_name,
        "away": snap.away_team_name,
        "kickoff": snap.date_local,
        "mercado": mercado,
        "linha": av.linha,
        "lado": _lado_de_linha(av.linha),
        "prob": av.prob,
        "confianca": av.confianca,
        "aprovada_motor": True,
        "decisao_oficial": decisao_oficial,
        "status_estatistico": status_info["status"],
        "motivo_status": status_info["motivo"],
        "origem_operacional": origem_op,
        "status_operacional": status_op,
        "timestamp_override": timestamp_override,
        "motivo_override": motivo_override,
        "riscos": list(av.riscos),
        "source": f"motor:{VERSAO_LIVE_OP}",
        # Campos live (do motor live, NÃO recalculados pela camada):
        "live_minute": av.minuto,            # None = NÃO DISPONÍVEL (NULL != ZERO)
        "score": av.placar,
        "status_live": av.status,
        "provider": "API-Football v3 (api-sports.io)",
        "odd": getattr(odd, "odd", None) if odd is not None else None,
        "classificacao": av.classificacao,
        "modo_teste": True,
        "prediction_timestamp": None,        # preenchido pelo caller (generated_at)
    }


def _construir_saida_live(varredura: Any, generated_at: str) -> SaidaOficial:
    """Constrói a SaidaOficial LIVE (modo teste) a partir da Varredura do
    motor live (scan_live_opportunities). Função pura da saída do motor --
    mesma entrada => mesma saída. NÃO recalcula prob/confianca/linha; apenas
    roteia pelo status estatístico + override. Override de modo NÃO cria
    sinal: só aprovadas do motor live (aprovada_motor=True) roteiam."""
    data_freshness = _live_freshness(getattr(varredura, "hora", ""))
    stale = (data_freshness == "STALE")

    # markets: status por mercado + contagens do motor live
    markets: dict[str, dict[str, Any]] = {}
    for mk, info in STATUS_MERCADOS.items():
        markets[mk] = {
            "status": info["status"], "motivo": info["motivo"],
            "avaliacoes_motor": 0, "aprovadas_motor": 0,
        }
    for cand in varredura.candidatos:
        for av in cand.avaliacoes:
            mk = av.mercado
            if mk in markets:
                markets[mk]["avaliacoes_motor"] += 1
    ap_por_mercado: dict[str, int] = {}
    for av in varredura.aprovadas:
        ap_por_mercado[av.mercado] = ap_por_mercado.get(av.mercado, 0) + 1
    for mk, n in ap_por_mercado.items():
        if mk in markets:
            markets[mk]["aprovadas_motor"] = n

    # mapa fixture_id -> Candidato (para snapshot/home/away)
    cand_by_fx = {c.snapshot.fixture_id: c for c in varredura.candidatos}

    operational: list[dict[str, Any]] = []
    observations: list[dict[str, Any]] = []
    blocked: list[dict[str, Any]] = []

    # Aprovadas pelo motor live, roteadas pelo status + override de mercado.
    for av in varredura.aprovadas:
        cand = cand_by_fx.get(av.fixture_id)
        if cand is None:
            continue  # sem candidato/snapshot seguro => não vira oportunidade
        entry = _entry_opp_live(cand, av, av.mercado)
        entry["prediction_timestamp"] = generated_at
        if stale:
            # Dado live desatualizado: NÃO vira oportunidade operacional.
            entry["decisao_oficial"] = "DADO_LIVE_DESATUALIZADO"
            entry["status_operacional"] = None
            entry["origem_operacional"] = None
            observations.append(entry)
            continue
        rota = _rota_mercado(av.mercado)
        if rota == "operational":
            operational.append(entry)
        elif rota == "observation":
            observations.append(entry)
        else:
            blocked.append(entry)

    # Observação (classe C) do motor live -- nunca operacional.
    for av in getattr(varredura, "observacao", []) or []:
        cand = cand_by_fx.get(av.fixture_id)
        if cand is None:
            continue
        entry = _entry_opp_live(cand, av, av.mercado)
        entry["aprovada_motor"] = False
        entry["decisao_oficial"] = "OBSERVACAO"
        entry["status_operacional"] = None
        entry["origem_operacional"] = None
        entry["prediction_timestamp"] = generated_at
        observations.append(entry)

    # Mercados não-pre-game (pressao_live, odds_roi): blocked status-only.
    for mk in STATUS_MERCADOS:
        if mk in _MERCADOS_PRE_GAME:
            continue
        info = STATUS_MERCADOS[mk]
        blocked.append({
            "fixture_id": None,
            "espec": "VARREDURA LIVE",
            "mercado": mk,
            "status_estatistico": info["status"],
            "motivo_status": info["motivo"],
            "decisao_oficial": _DECISAO_OFICIAL_POR_STATUS[info["status"]],
            "source": "registro_status_mercados",
        })

    nenhum = len(operational) == 0
    total_ao_vivo = getattr(varredura, "total_ao_vivo", 0)
    proj_hash = _projeto_hash()
    live_block = {
        "total_ao_vivo": total_ao_vivo,
        "total_elegiveis": getattr(varredura, "total_elegiveis", 0),
        "total_triados": getattr(varredura, "total_triados", 0),
        "status_estatistico": BLOQUEADO,
        "status_operacional": STATUS_OP_LIVE_TESTE,
        "override_operador": "true",
        "decisao_humana": "true",
        "timestamp_override": TIMESTAMP_OVERRIDE_LIVE,
        "motivo_override": MOTIVO_OVERRIDE_LIVE,
        "data_freshness": data_freshness,
        "versao_projeto": proj_hash,
    }
    return SaidaOficial(
        fixture_id=None,
        espec="VARREDURA LIVE",
        generated_at=generated_at,
        engine_version=VERSAO_LIVE_OP,
        camada_version=VERSAO_CAMADA_LIVE,
        projeto_hash=proj_hash,
        data_status=("LIVE" if total_ao_vivo > 0 else "SEM_JOGO_LIVE"),
        markets=markets,
        operational_opportunities=operational,
        observations=observations,
        blocked=blocked,
        provenance={
            "motor": VERSAO_LIVE_OP,
            "camada": VERSAO_CAMADA_LIVE,
            "projeto_hash": proj_hash,
            "generated_at": generated_at,
            "fonte_dados": "API-Football v3 (api-sports.io)",
            "nota": (
                "LIVE em MODO TESTE por override explicito do operador. "
                "NÃO validado estatisticamente (pressao_live BLOQUEADO). "
                "Override de MODO NÃO cria sinal: só aparecem oportunidades "
                "que o motor live retornar ENTRAR (aprovada_motor=True). "
                "GOALS (estatístico) e CORNERS (override de mercado) "
                "roteados como no pré-live. Sem aposta financeira."
            ),
        },
        nenhum_aprovado=nenhum,
        mode="live",
        modo_teste=True,
        live=live_block,
    )


def varredura_live(
    client: Any, mercados: tuple[str, ...] | None = None,
) -> SaidaOficial:
    """Saída oficial canônica LIVE (modo teste) da varredura ao vivo.

    Consome scan_live_opportunities (motor live, READ: não registra no
    registro de validação, não grava histórico de validação) e roteia pelo
    status estatístico + override. Modo teste: override de MODO não cria
    sinal; só oportunidades que o motor live retornar ENTRAR. Nenhuma aposta
    financeira executada/integrada.
    """
    from src.live_opportunity import scan_live_opportunities
    from src.policy import classificar_liga

    generated_at = now_brt().strftime("%Y-%m-%d %H:%M:%S")
    varredura = scan_live_opportunities(client, mercados=mercados)

    # Política forte (mesmo filtro de universo do cmd_aovivoop): aprovadas
    # de competicoes EXCLUIDA não são operacionais. Cálculo do motor intacto.
    mantidas = []
    for av in varredura.aprovadas:
        cand = next((c for c in varredura.candidatos
                     if c.snapshot.fixture_id == av.fixture_id), None)
        if cand is None:
            mantidas.append(av)
            continue
        classe, _motivo = classificar_liga(
            cand.snapshot.league_id, cand.snapshot.league_name)
        if classe != "EXCLUIDA":
            mantidas.append(av)
    varredura.aprovadas = mantidas

    return _construir_saida_live(varredura, generated_at)


def formatar_saida_live(saida: SaidaOficial) -> str:
    """Resumo textual legível da SaidaOficial LIVE (modo teste). Não substitui
    o dict canônico (to_dict/--json)."""
    L: list[str] = []
    L.append(
        f"=== SAÍDA OFICIAL LIVE · {saida.engine_version} "
        f"(camada {saida.camada_version}) ===")
    L.append(
        f"mode={saida.mode} · modo_teste={saida.modo_teste} "
        f"· data_status={saida.data_status}")
    L.append(
        f"generated_at: {saida.generated_at} · projeto_hash: {saida.projeto_hash}")
    lv = saida.live or {}
    L.append(
        f"live: ao_vivo={lv.get('total_ao_vivo')} "
        f"elegiveis={lv.get('total_elegiveis')} "
        f"triados={lv.get('total_triados')} frescor={lv.get('data_freshness')}")
    L.append(
        f"       status_estat={lv.get('status_estatistico')} "
        f"status_op={lv.get('status_operacional')} "
        f"override={lv.get('override_operador')}")
    L.append("")
    L.append("-- status por mercado --")
    for mk, info in saida.markets.items():
        L.append(
            f"  {mk:14s} {info['status']:24s} "
            f"av={info['avaliacoes_motor']} aprov_motor={info['aprovadas_motor']}")
    L.append("")
    if saida.operational_opportunities:
        L.append("-- OPORTUNIDADES OPERACIONAIS LIVE (MODO TESTE) --")
        for o in saida.operational_opportunities:
            origem = o.get("origem_operacional", "estatistico")
            tag = "OVERRIDE" if origem == "override_usuario" else "ESTATISTICO"
            odd = o.get("odd")
            odd_txt = f" odd={odd}" if odd else ""
            minuto = o.get("live_minute")
            minuto_txt = "NÃO DISPONÍVEL" if minuto is None else f"{minuto}'"
            L.append(
                f"  [{tag}] {o.get('espec')} {minuto_txt} {o.get('score')} | "
                f"{o['mercado']} | {o['linha']} | prob={o['prob']} "
                f"conf={o['confianca']}{odd_txt} | {o['decisao_oficial']} "
                f"(status_estat={o['status_estatistico']})")
    else:
        if (saida.live or {}).get("total_ao_vivo", 0) == 0:
            L.append("-- NENHUM JOGO AO VIVO ELEGÍVEL DISPONÍVEL PARA TESTE AGORA --")
        else:
            L.append("-- OPORTUNIDADES OPERACIONAIS LIVE: NENHUMA APROVADA PELO MOTOR --")
    if (saida.live or {}).get("data_freshness") == "STALE":
        L.append(
            "[AVISO] DADO LIVE DESATUALIZADO — nenhuma oportunidade operacional gerada.")
    if saida.observations:
        L.append("")
        L.append(f"-- OBSERVAÇÕES LIVE: {len(saida.observations)} --")
        for o in saida.observations[:20]:
            minuto = o.get("live_minute")
            minuto_txt = "NÃO DISPONÍVEL" if minuto is None else f"{minuto}'"
            L.append(
                f"  {o.get('espec')} {minuto_txt} | {o.get('mercado')} | "
                f"{o.get('linha')} | {o['decisao_oficial']}")
    if saida.blocked:
        L.append("")
        L.append("-- BLOQUEADOS / NÃO AVALIÁVEIS --")
        for b in saida.blocked:
            L.append(
                f"  {b['mercado']:14s} {b['decisao_oficial']} "
                f"({b['status_estatistico']})")
    return "\n".join(L)