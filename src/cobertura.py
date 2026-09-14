"""MATRIZ DE COBERTURA POR COMPETICAO x MERCADO x MODO (PRE_GAME / LIVE).

Camada de ELEGIBILIDADE construida a partir da AUDITORIA TECNICA DA
API-FOOTBALL de 08/09/2026 (C:\\CornerIntelligence\\AUDITORIA_API_FOOTBALL_
2026-09-08.md). A plataforma NUNCA mais permite ou bloqueia uma competicao
INTEIRA por regra generica: ela sabe, por COMPETICAO x MERCADO x MODO, se
os dados reais da fonte sustentam analise/recomendacao.

O QUE ESTA CAMADA FAZ:
  - classifica cada combinacao (competicao, mercado, modo) em classes
    A (excelente) / B (boa) / C (parcial - observacao) / D (insuficiente)
    / E (sem dados uteis);
  - valida DINAMICAMENTE o fixture real (pre-jogo: historico; live:
    snapshot atual) ANTES de qualquer calculo caro;
  - status final: PERMITIDO (A/B com dados confirmados), OBSERVACAO
    (classe C: analise/calibracao permitida, NUNCA recomendacao
    automatica) ou BLOQUEADO (D/E ou dado essencial ausente).

O QUE ESTA CAMADA NAO FAZ (regras do operador):
  - NAO recalibra probabilidades, NAO altera thresholds, NAO altera
    formulas dos mercados, NAO mexe no ledger/registro, NAO registra
    recomendacoes, NAO altera a politica de selecao operacional. A
    suficiencia de amostra continua sendo a logica JA EXISTENTE de
    confianca/auditoria (nenhum threshold novo e criado aqui).

REGRA ABSOLUTA: DADO AUSENTE != ZERO. Nenhuma funcao deste modulo
converte None/ausencia em zero; a ausencia e sempre explicitada no
motivo e no detalhe do veredicto.
"""

from dataclasses import dataclass, field
from typing import Any

# ----------------------------------------------------------------------
# Constantes publicas
# ----------------------------------------------------------------------
MODO_PRE_GAME = "PRE_GAME"
MODO_LIVE = "LIVE"
MODOS = (MODO_PRE_GAME, MODO_LIVE)

MERCADO_GOALS = "GOALS"
MERCADO_CORNERS = "CORNERS"
MERCADO_CARDS = "CARDS"
MERCADOS = (MERCADO_GOALS, MERCADO_CORNERS, MERCADO_CARDS)

STATUS_PERMITIDO = "PERMITIDO"
STATUS_OBSERVACAO = "OBSERVACAO"
STATUS_BLOQUEADO = "BLOQUEADO"

# Classes -> status estrutural. A e B: analise e recomendacao permitidas.
# C: apenas observacao/calibracao (nunca recomendacao automatica).
# D e E: bloqueados.
_STATUS_POR_CLASSE = {
    "A": STATUS_PERMITIDO,
    "B": STATUS_PERMITIDO,
    "C": STATUS_OBSERVACAO,
    "D": STATUS_BLOQUEADO,
    "E": STATUS_BLOQUEADO,
}

# Familias de mercado do motor -> mercado da matriz. A familia RESULTADO
# (1X2/DC/DNB/AH) deriva do MESMO dado base da familia GOLS (placar /
# projetacao de gols): segue o veredicto de GOALS.
MERCADO_DA_FAMILIA = {
    "escanteios": MERCADO_CORNERS,
    "gols": MERCADO_GOALS,
    "cartoes": MERCADO_CARDS,
    "resultado": MERCADO_GOALS,
}

# Ordem canonica das familias do motor (deep dive)
TODAS_FAMILIAS = ("escanteios", "gols", "cartoes", "resultado")

# Status ao vivo em que mercados de 90 minutos sao analisaveis
_STATUS_LIVE_VALIDO = {"1H", "HT", "2H"}


# ----------------------------------------------------------------------
# Matriz ESTRUTURAL: evidencia da auditoria de 08/09/2026
# ----------------------------------------------------------------------
# Classe A: estatisticas de partida entregues em >= 95% das partidas
# amostradas no cache (0 custo de API).
_CLASSE_A = (
    71,    # Brasileirao Serie A (261/261)
    72,    # Serie B BR (46/46)
    73,    # Copa do Brasil (40/41 na auditoria; 88/150 na temporada 2026 -
           # cobertura parcial: validacao dinamica OBRIGATORIA, vide nota)
    2,     # Champions League (45/90 em 2026; vazios = jogos futuros; live ok)
    3,     # Europa League (14/14)
    848,   # Conference League (5/6)
    39,    # Premier League (34/36)
    140,   # La Liga (45/45)
    135,   # Serie A ITA (83/83)
    78,    # Bundesliga (7/7)
    61,    # Ligue 1 (13/13)
    88,    # Eredivisie (73/73; live verificado na auditoria)
    94,    # Primeira Liga POR (73/73)
    203,   # Super Lig TUR (93/93)
    204,   # 1. Lig TUR (113/113)
    106,   # Ekstraklasa POL (60/60)
    113,   # Allsvenskan SUE (161/161)
    144,   # Jupiler Pro League BEL (15/15)
    128,   # Liga Profesional ARG (374/378)
    262,   # Liga MX (68/68)
    307,   # Saudi Pro League (48/49; live verificado na auditoria)
    281,   # Primera Division URU (224/225)
    239,   # Primera A COL (275/276)
    253,   # Major League Soccer (341/343 em 2026; auditoria 09/09/2026)
    242,   # Serie A EQU (227/232)
    252,   # Division Profesional PAR (52/56)
    13,    # Libertadores (39/39)
    11,    # Sudamericana (34/34)
    772,   # Leagues Cup (60/61)
    89,    # Eerste Divisie HOL (95/97)
    475,   # Paulistao A1 (26/26; odds pre nao fornecidas)
    624,   # Carioca (17/17; odds pre nao fornecidas)
)

# Classe B: 100% de entrega na AMOSTRA PEQUENA coletada (a auditoria nao
# viu falha, mas a amostra e curta para classe A).
_CLASSE_B = (
    233, 114, 141, 41, 95, 136, 137, 16, 197, 344, 383, 172, 119, 62, 105,
)

# Classe C: cobertura PARCIAL (~50% das partidas com estatisticas).
_CLASSE_C = (
    479,   # Canadian Premier League (41/81)
    82,    # Frauen Bundesliga (11/23)
    45,    # FA Cup
    134,   # Torneo Federal A ARG
)

# Classe E (auditoria): ZERO estatisticas de partida na fonte. Os PLACARES
# existem (a liga e real e os jogos tem resultado), mas escanteios/cartoes/
# estatisticas nao sao entregues. NUNCA classificada por regra generica:
# cada mercado recebe a classe que a evidencia sustenta.
_CLASSE_E = (
    75,    # Serie C BR (0/196)
    76,    # Serie D BR (coverage oficial statistics_fixtures=false)
    129,   # Primera Nacional ARG (0/78)
    130,   # Copa Argentina (0/8)
    241,   # Copa Colombia (0/58)
    205,   # 2. Lig TUR (0/14)
    219,   # 2. Liga AUT (0/33)
    138, 943,
    667,   # Amistosos de selecoes (0/210)
    477,   # Gaucho (0/11)
    629,   # Mineiro (0/15)
    606,   # Paranaense (0/10)
    604,   # Catarinense (0/10)
    627,   # Paraense (0/10)
    612,   # Copa do Nordeste (0/4)
    290, 673,
    131, 132,  # Primera C / Primera B MET ARG
    887,
)

# Notas especiais da auditoria (entram no motivo do veredicto)
_NOTA_ESPECIAL = {
    73: "Copa do Brasil: cobertura parcial na temporada 2026 (88/150 "
        "partidas com estatisticas; fases iniciais sem entrega na fonte "
        "- 62 excluidas da amostra, nunca zeradas): analise somente "
        "quando a validacao dinamica confirmar dados suficientes no "
        "jogo e na fase",
    76: "Serie D BR: coverage oficial statistics_fixtures=false; "
        "eventos so com gols/VAR; sem grupos em standings",
    75: "Serie C BR: 0 de 196 partidas com estatisticas na auditoria",
    667: "Amistosos de selecoes: placares existem, zero estatisticas "
         "de partida",
    475: "Paulistao A1: estatisticas OK; odds pre-jogo nao fornecidas "
         "pela fonte (nao afeta analise de dados)",
    624: "Carioca: estatisticas OK; odds pre-jogo nao fornecidas pela "
         "fonte (nao afeta analise de dados)",
    2: "UCL: jogos futuros vem sem estatisticas por definicao (live "
       "verificado com entrega integral na auditoria)",
}

_CLASSE_AUDITORIA: dict[int, str] = {}
for _lid in _CLASSE_A:
    _CLASSE_AUDITORIA[_lid] = "A"
for _lid in _CLASSE_B:
    _CLASSE_AUDITORIA[_lid] = "B"
for _lid in _CLASSE_C:
    _CLASSE_AUDITORIA[_lid] = "C"
for _lid in _CLASSE_E:
    _CLASSE_AUDITORIA[_lid] = "E"


def _nota_base(classe: str, league_id: int | None) -> str:
    nota = _NOTA_ESPECIAL.get(league_id)
    base = {
        "A": "estatisticas entregues em >=95% das partidas amostradas",
        "B": "estatisticas em 100% da amostra pequena coletada",
        "C": "estatisticas em ~50% das partidas amostradas",
        "E": "zero estatisticas de partida na auditoria",
    }.get(classe, "cobertura desconhecida")
    if nota:
        return f"auditoria 08/09/2026 - {nota}"
    return f"auditoria 08/09/2026 - {base}"


def classe_estrutural(
    league_id: int | None, league_name: str | None,
    mercado: str, modo: str,
) -> tuple[str, str]:
    """(classe, motivo) da matriz ESTRUTURAL para uma combinacao.

    NAO olha o fixture: e a evidencia da liga acumulada na auditoria.
    Liga fora da auditoria => classe C (observacao): nunca se ASSUME
    cobertura, mas tambem nao se bloqueia antes de validar a amostra
    real do fixture.
    """
    base = _CLASSE_AUDITORIA.get(league_id) if league_id else None

    if base is None:
        return "C", (
            "competicao sem evidencia na auditoria de cobertura "
            "(08/09/2026): classe C por honestidade - analise somente "
            "em observacao ate validar amostra propria; ausencia de "
            "evidencia nunca e convertida em zero nem em permissao"
        )

    if base in ("A", "B"):
        return base, _nota_base(base, league_id)

    if base == "C":
        # cobertura parcial: placares completos, estatisticas pela metade
        if mercado == MERCADO_GOALS:
            if modo == MODO_PRE_GAME:
                return "B", _nota_base(base, league_id) + (
                    "; placares completos no pre-jogo"
                )
            return "C", _nota_base(base, league_id) + (
                "; entrega live nao confirmada nesta liga"
            )
        return "C", _nota_base(base, league_id)

    # base == "E": liga SEM estatisticas de partida. Por mercado e modo
    # a evidencia sustenta classes DIFERENTES (nunca regra generica):
    if mercado == MERCADO_GOALS:
        if modo == MODO_PRE_GAME:
            # placares/mando/amostra existem na fonte (auditoria):
            # GOALS PRE e AVALIAVEL pelo historico de placares
            return "B", _nota_base(base, league_id) + (
                "; placares completos: gols pre-jogo avaliavel pelo "
                "historico de placares"
            )
        # live: placar existe, mas sem estatisticas de apoio =>
        # somente observacao
        return "C", _nota_base(base, league_id) + (
            "; placar live existe, sem estatisticas de apoio"
        )
    if modo == MODO_PRE_GAME and mercado == MERCADO_CARDS:
        # eventos historicos sem cartoes completos (auditoria Serie D:
        # eventos so gols/VAR) => dado insuficiente
        return "D", _nota_base(base, league_id) + (
            "; eventos historicos sem cartoes completos"
        )
    # escanteios pre/live e cartoes live: sem estatistica nenhuma na fonte
    return "E", _nota_base(base, league_id)


# ----------------------------------------------------------------------
# Veredicto
# ----------------------------------------------------------------------
@dataclass
class VeredictoCobertura:
    """Veredicto de UMA combinacao competicao x mercado x modo.

    `classe` e a classe estrutural (auditoria) da combinacao; `status`
    e o resultado FINAL apos a validacao dinamica do fixture real.
    """

    competicao: str
    modo: str
    mercado: str
    classe: str
    status: str
    motivo: str
    # contagens REAIS usadas na validacao dinamica (ausencia nunca vira
    # zero: campos ausentes permanecem None aqui tambem)
    detalhe: dict[str, Any] = field(default_factory=dict)

    @property
    def permitido(self) -> bool:
        return self.status == STATUS_PERMITIDO

    @property
    def observacao(self) -> bool:
        return self.status == STATUS_OBSERVACAO

    @property
    def bloqueado(self) -> bool:
        return self.status == STATUS_BLOQUEADO


def _veredicto_estrutural(
    league_id: int | None, league_name: str | None,
    mercado: str, modo: str,
) -> VeredictoCobertura:
    classe, motivo = classe_estrutural(league_id, league_name, mercado, modo)
    return VeredictoCobertura(
        competicao=league_name or (f"liga {league_id}" if league_id else "s/d"),
        modo=modo, mercado=mercado, classe=classe,
        status=_STATUS_POR_CLASSE[classe], motivo=motivo,
    )


# ----------------------------------------------------------------------
# Validacao dinamica PRE_GAME: o que o HISTORICO real do fixture sustenta
# ----------------------------------------------------------------------
def _jogos(hist: dict[str, Any]) -> list[Any]:
    return list(hist.get("games_home") or []) + list(hist.get("games_away") or [])


def avaliar_cobertura_pre(
    league_id: int | None, league_name: str | None,
    hist: dict[str, Any] | None,
) -> list[VeredictoCobertura]:
    """Veredictos PRE_GAME por mercado a partir do historico coletado.

    Requisitos por mercado (ETAPA 2 da especificacao):
      - GOALS: jogos encerrados com placar completo (gols pro/contra) na
        amostra coletada;
      - CORNERS: jogos com Corner Kicks na amostra; se o historico NAO
        possui Corner Kicks, a ausencia NUNCA vira zero => BLOQUEADO;
      - CARDS: jogos com cartoes completos (amarelos e vermelhos dos
        dois lados); inconsistencia reduz a classe, insuficiencia
        bloqueia.

    Nenhum threshold novo: a suficiencia de AMOSTRA continua sendo
    julgada pela confianca/auditoria JA EXISTENTES; aqui somente se
    exige que exista AO MENOS um jogo com o dado do mercado (senao nao
    ha nada a analisar - e isso e bloqueio, nao calibracao).
    """
    hist = hist or {}
    games = _jogos(hist)
    sem_stats = (hist.get("sem_estatisticas_home") or 0) + (
        hist.get("sem_estatisticas_away") or 0
    )
    base_det = {
        "jogos_com_estatisticas": len(games),
        "jogos_sem_estatisticas_na_fonte": sem_stats,
    }

    def _com_gols(g: Any) -> bool:
        return g.goals_for is not None and g.goals_against is not None

    def _com_escanteios(g: Any) -> bool:
        return g.corners_total is not None

    def _com_cartoes(g: Any) -> bool:
        return None not in (g.yellow_for, g.yellow_against,
                            g.red_for, g.red_against)

    requisitos = {
        MERCADO_GOALS: ("placar completo (gols pro/contra)", _com_gols),
        MERCADO_CORNERS: ("Corner Kicks", _com_escanteios),
        MERCADO_CARDS: ("cartoes completos (amarelos e vermelhos)", _com_cartoes),
    }

    out: list[VeredictoCobertura] = []
    for mercado in MERCADOS:
        v = _veredicto_estrutural(league_id, league_name, mercado,
                                  MODO_PRE_GAME)
        if v.bloqueado:
            v.detalhe = dict(base_det)
            out.append(v)
            continue

        desc, tem_dado = requisitos[mercado]
        com_dados = [g for g in games if tem_dado(g)]
        v.detalhe = dict(base_det)
        v.detalhe["jogos_com_dados_do_mercado"] = len(com_dados)

        if not com_dados:
            v.status = STATUS_BLOQUEADO
            v.motivo = (
                f"DADO INSUFICIENTE: nenhum jogo do historico coletado "
                f"possui {desc} (0 de {len(games)} jogos com estatistica; "
                f"{sem_stats} sem estatistica na fonte). Ausencia nunca e "
                f"convertida em zero - mercado bloqueado neste fixture"
            )
        elif v.status == STATUS_PERMITIDO:
            v.motivo += (
                f"; validado no fixture: {len(com_dados)} jogos com "
                f"{desc} na amostra coletada"
            )
        else:  # classe C continua em observacao mesmo com dados
            v.motivo += (
                f"; {len(com_dados)} jogos com {desc} na amostra coletada "
                f"(classe C: somente observacao, nunca recomendacao)"
            )
        out.append(v)
    return out


# ----------------------------------------------------------------------
# Validacao dinamica LIVE: o que o SNAPSHOT atual sustenta
# ----------------------------------------------------------------------
def avaliar_cobertura_live(snapshot: Any) -> list[VeredictoCobertura]:
    """Veredictos LIVE por mercado a partir do snapshot atual.

    Requisitos por mercado (ETAPA 3 da especificacao):
      - GOALS: status + minuto validos, placar presente e eventos ou
        estatisticas live; sem estatisticas de apoio => apenas
        OBSERVACAO (nunca recomendacao);
      - CORNERS: estatisticas nao vazias COM Corner Kicks validos para
        AS DUAS equipes nesta leitura; falta de qualquer essencial =>
        DADO INSUFICIENTE, nao recomendar;
      - CARDS: amarelos E vermelhos dos dois lados (estatisticas live).
    """
    snap = snapshot
    out: list[VeredictoCobertura] = []

    for mercado in MERCADOS:
        v = _veredicto_estrutural(
            snap.league_id, snap.league_name, mercado, MODO_LIVE)
        if v.bloqueado:
            v.detalhe = {"has_stats": snap.has_stats,
                         "n_events": len(snap.events or [])}
            out.append(v)
            continue

        faltas: list[str] = []
        if mercado == MERCADO_GOALS:
            v.detalhe = {
                "status": snap.status, "minuto": snap.elapsed,
                "placar": (None if snap.goals_home is None
                           or snap.goals_away is None
                           else f"{snap.goals_home}-{snap.goals_away}"),
                "has_stats": snap.has_stats,
                "n_events": len(snap.events or []),
            }
            if snap.status not in _STATUS_LIVE_VALIDO:
                faltas.append(f"status {snap.status} fora da janela de "
                              f"jogo regulamentar analisavel")
            if snap.elapsed is None:
                faltas.append("minuto nao informado na fonte")
            if snap.goals_home is None or snap.goals_away is None:
                faltas.append("placar ausente nesta leitura")
            if not snap.has_stats and not (snap.events or []):
                faltas.append("sem estatisticas nem eventos live na fonte")
            if (not faltas) and snap.has_stats:
                v.motivo += "; dados live confirmados nesta leitura"
            elif not faltas:
                v.status = STATUS_OBSERVACAO
                v.motivo += ("; sem estatisticas live de apoio nesta "
                             "leitura (so placar/eventos): apenas "
                             "observacao")

        elif mercado == MERCADO_CORNERS:
            corner_h = snap.stats_home.get("Corner Kicks")
            corner_a = snap.stats_away.get("Corner Kicks")
            v.detalhe = {"corner_kicks_casa": corner_h,
                         "corner_kicks_fora": corner_a,
                         "has_stats": snap.has_stats}
            if corner_h is None and corner_a is None:
                faltas.append(
                    "DADO INSUFICIENTE: Corner Kicks ausentes para os "
                    "dois lados nesta leitura (ausencia nunca vira zero)")
            elif corner_h is None or corner_a is None:
                faltas.append(
                    "DADO INSUFICIENTE: Corner Kicks de apenas UM dos "
                    "lados nesta leitura; a matriz exige atualizacao "
                    "valida para AS DUAS equipes")
            else:
                v.motivo += "; Corner Kicks das duas equipes nesta leitura"

        else:  # MERCADO_CARDS
            yc_h = snap.stats_home.get("Yellow Cards")
            yc_a = snap.stats_away.get("Yellow Cards")
            rc_h = snap.stats_home.get("Red Cards")
            rc_a = snap.stats_away.get("Red Cards")
            v.detalhe = {
                "amarelos_casa": yc_h, "amarelos_fora": yc_a,
                "vermelhos_casa": rc_h, "vermelhos_fora": rc_a,
                "has_stats": snap.has_stats,
            }
            if None in (yc_h, yc_a, rc_h, rc_a):
                faltas.append(
                    "DADO INSUFICIENTE: cartoes incompletos na fonte "
                    "(exigidos amarelos e vermelhos de AMBOS os lados; "
                    "ausencia nunca vira zero)")
            else:
                v.motivo += "; cartoes completos das duas equipes"

        if faltas:
            v.status = STATUS_BLOQUEADO
            v.motivo = "; ".join(faltas)
        out.append(v)
    return out


# ----------------------------------------------------------------------
# Consultas auxiliares para os fluxos
# ----------------------------------------------------------------------
def veredictos_por_mercado(
    veredictos: list[VeredictoCobertura],
) -> dict[str, VeredictoCobertura]:
    return {v.mercado: v for v in veredictos}


def status_do_mercado(
    veredictos: list[VeredictoCobertura], mercado: str,
) -> str | None:
    """Status do veredicto de um mercado da matriz (GOALS/CORNERS/CARDS)."""
    for v in veredictos:
        if v.mercado == mercado:
            return v.status
    return None


def status_da_familia(
    veredictos: list[VeredictoCobertura], familia: str,
) -> str | None:
    """Status de cobertura de uma FAMILIA do motor (resultado => GOALS)."""
    mercado = MERCADO_DA_FAMILIA.get(familia)
    if mercado is None:
        return None
    return status_do_mercado(veredictos, mercado)


def filtrar_avaliacoes_por_cobertura(
    avaliacoes: list[Any],
    veredictos: list[VeredictoCobertura],
) -> tuple[list[Any], list[tuple[Any, VeredictoCobertura | None]]]:
    """Separa avaliacoes (mantidas, bloqueadas) pelo veredicto do mercado.

    BLOQUEADO sai do fluxo ANTES de qualquer calculo de probabilidade/
    confianca. PERMITIDO e OBSERVACAO seguem (observacao e o modo de
    calibracao); quem impede a recomendacao automatica da classe C e o
    fluxo operacional, nao esta funcao.
    """
    mantidas, bloqueadas = [], []
    for av in avaliacoes:
        mercado = MERCADO_DA_FAMILIA.get(getattr(av, "mercado", ""))
        ver = next((v for v in veredictos if v.mercado == mercado), None) \
            if mercado else None
        if ver is not None and ver.bloqueado:
            bloqueadas.append((av, ver))
        else:
            mantidas.append(av)
    return mantidas, bloqueadas


def familias_permitidas(
    veredictos: list[VeredictoCobertura],
) -> set[str]:
    """Familias do motor com cobertura PERMITIDA ou OBSERVACAO."""
    por = veredictos_por_mercado(veredictos)
    return {
        familia for familia in TODAS_FAMILIAS
        if (ver := por.get(MERCADO_DA_FAMILIA[familia])) is not None
        and not ver.bloqueado
    }


def familias_para_deep_dive(
    veredictos: list[VeredictoCobertura],
    filtro_operador: tuple[str, ...] | None = None,
) -> tuple[str, ...] | None:
    """Familias que entram no deep dive: matriz ∩ filtro do operador.

    Retorna None quando NADA restringe (comportamento integral do
    motor, familias todas liberadas) - os calculos nao percebem a
    matriz. Lista vazia => nenhum mercado tem dados suficientes: o
    deep dive e economizado (filtro ANTES do processamento caro).
    """
    liberadas = familias_permitidas(veredictos)
    if filtro_operador is not None:
        liberadas &= set(filtro_operador)
    if len(liberadas) == len(TODAS_FAMILIAS):
        return None
    return tuple(f for f in TODAS_FAMILIAS if f in liberadas)


def resumo_cobertura(veredictos: list[VeredictoCobertura]) -> str:
    """Resumo compacto 'mercado=STATUS(...)' para motivos de descarte."""
    partes = []
    for v in veredictos:
        if v.bloqueado:
            motivo = v.motivo.split(";")[0].strip()
            partes.append(f"{v.mercado}={v.status} ({motivo})")
        else:
            partes.append(f"{v.mercado}={v.status}")
    return "; ".join(partes)


# ----------------------------------------------------------------------
# Relatorio de elegibilidade (formato exigido: COMPETICAO | MODO |
# MERCADO | CLASSE | STATUS | MOTIVO)
# ----------------------------------------------------------------------
def formatar_relatorio_cobertura(
    veredictos: list[VeredictoCobertura],
) -> list[str]:
    out = [
        "[COBERTURA] MATRIZ DE ELEGIBILIDADE (COMPETICAO | MODO | "
        "MERCADO | CLASSE | STATUS | MOTIVO):"
    ]
    for v in veredictos:
        out.append(
            f"  {v.competicao} | {v.modo} | {v.mercado} | {v.classe} | "
            f"{v.status} | {v.motivo}"
        )
    return out


def resumo_matriz() -> dict[str, Any]:
    """Totais da matriz estrutural por (modo, mercado, classe) - base
    para o relatorio final de whitelists/blacklists da ETAPA 13."""
    contagens: dict[tuple[str, str, str], int] = {}
    for league_id in sorted(_CLASSE_AUDITORIA):
        for modo in MODOS:
            for mercado in MERCADOS:
                classe, _ = classe_estrutural(
                    league_id, None, mercado, modo)
                chave = (modo, mercado, classe)
                contagens[chave] = contagens.get(chave, 0) + 1

    return {
        "ligas_na_auditoria": len(_CLASSE_AUDITORIA),
        "por_classe_base": {
            base: sum(1 for c in _CLASSE_AUDITORIA.values() if c == base)
            for base in ("A", "B", "C", "D", "E")
        },
        "contagens": {f"{modo}|{mercado}|{classe}": n
                      for (modo, mercado, classe), n in sorted(contagens.items())},
        "desconhecidas": (
            "ligas fora da auditoria => classe C (observacao) em todos "
            "os mercados/modos; nunca recomendacao automatica"
        ),
    }