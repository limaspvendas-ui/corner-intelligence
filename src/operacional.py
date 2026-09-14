"""CAMADA OPERACIONAL OFICIAL do Corner Intelligence.

Fechamento operacional: expoe UMA saída oficial canônica que consome o motor
existente (src/prejogo_opportunity.py, src/policy.py, src/cobertura.py) SEM
duplicar nenhuma lógica matemática (Poisson, thresholds, confianca, settlement,
calibração). A IA (Claude Code, API, MCP, ChatGPT futuramente) consulta esta
saída; a IA NÃO substitui o motor -- interpreta o resultado oficial.

PRINCÍPIOS INEGOCIÁVEIS (fechamento operacional):
  - UM motor. Esta camada consome a saída já existente; não recalcula
    prob/confianca/linha/edge. Os valores exibidos vêm direto do motor.
  - Ação operacional somente onde JÁ existe aprovação estatística.
    Hoje: somente GOALS (APROVADO_PARA_PROXIMA_FASE).
  - Mercados não aprovados são expostos para OBSERVAÇÃO ou BLOQUEADOS, nunca
    como oportunidade operacional aprovada. Nunca promover por cobertura.
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
VERSAO_CAMADA_OPERACIONAL = "operacional-1.0"

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

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _entry_opp(
    varredura: VarreduraPreJogo, av: Any, mercado: str,
) -> dict[str, Any]:
    """Constrói uma entrada a partir de uma AvaliacaoPre do motor.
    NÃO altera prob/confianca/linha -- copia direto do motor."""
    fx = varredura.fixture
    status_info = STATUS_MERCADOS.get(mercado, {"status": NAO_AVAL, "motivo": ""})
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
        "decisao_oficial": _DECISAO_OFICIAL_POR_STATUS.get(
            status_info["status"], "BLOQUEADO"),
        "status_estatistico": status_info["status"],
        "motivo_status": status_info["motivo"],
        "prediction_timestamp": None,  # preenchido pelo caller (generated_at)
        "riscos": list(av.riscos),
        "source": f"motor:{VERSAO_PREJOGO_OP}",
    }


def _rota_mercado(mercado: str) -> str:
    """Roteia um mercado para operational / observation / blocked pelo status."""
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
        rota = _rota_mercado(av.mercado)
        if rota in ("observation", "blocked"):
            entry = _entry_opp(varredura, av, av.mercado)
            entry["aprovada_motor"] = False
            entry["prediction_timestamp"] = generated_at
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
                "Apenas GOALS (APROVADO_PARA_PROXIMA_FASE) vira oportunidade "
                "operacional. Demais mercados: observacao ou bloqueado."
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
                "Apenas GOALS (APROVADO_PARA_PROXIMA_FASE) vira oportunidade "
                "operacional. Resultado/Corners em observacao. "
                "Cards/Live/ROI bloqueados ou nao avaliaveis."
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
        L.append("-- OPORTUNIDADES OPERACIONAIS (GOALS aprovado) --")
        for o in saida.operational_opportunities:
            L.append(f"  {o['mercado']} | {o['linha']} | prob={o['prob']} conf={o['confianca']} | {o['decisao_oficial']}")
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
        L.append("-- OPORTUNIDADES OPERACIONAIS (GOALS aprovado) --")
        for o in res["operational_opportunities"]:
            L.append(f"  {o['espec']} | {o['mercado']} | {o['linha']} | prob={o['prob']} conf={o['confianca']}")
    else:
        L.append("-- NENHUMA OPORTUNIDADE OPERACIONAL APROVADA --")
    if res["observations"]:
        L.append("")
        L.append(f"-- OBSERVAÇÕES (EM_OBSERVAÇÃO): {len(res['observations'])} --")
    if res["blocked"]:
        L.append(f"-- BLOQUEADOS/NÃO AVALIÁVEIS: {len(res['blocked'])} --")
    return "\n".join(L)