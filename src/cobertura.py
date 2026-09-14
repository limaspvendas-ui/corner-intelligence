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
    # 848 (Conference League) RECLASSIFICADA para C em 13/09/2026: amostra
    # maior (263 jogos encerrados) revelou 128 vazios = 51% sem estatisticas.
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
    # 252 (Paraguai) RECLASSIFICADA de A para B em 13/09/2026: 52/56 = 93%
    # (< 95%); borda inferior da classe A. Validacao dinamica obrigatoria.
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
    # ETAPA 4 (13/09/2026): 105 removido (e NM Cupen, copa norueguesa com
    # 0 estatisticas - mapeado por erro como "Eliteserien"; Eliteserien
    # real = 103). 103 adicionado (28/28 com estatisticas). 252 rebaixado
    # de A (93% < 95%).
    233, 114, 141, 41, 95, 136, 137, 16, 197, 344, 383, 172, 119, 62,
    103,  # Eliteserien (Noruega) - 28/28 jogos com estatisticas (100%)
    252,  # Division Profesional Paraguai - 52/56 (93%, borda inferior)
)

# Classe C: cobertura PARCIAL (~50% das partidas com estatisticas).
_CLASSE_C = (
    479,   # Canadian Premier League (41/81)
    82,    # Frauen Bundesliga (11/23)
    45,    # FA Cup
    134,   # Torneo Federal A ARG
    848,   # UEFA Conference League - 135/263 finalizados com stats (51%);
           # 128 vazios em jogos ENCERRADOS (auditoria 13/09/2026). Era A
           # (5/6 na previa); amostra maior revelou ~50% de vazios.
           # Validacao dinamica OBRIGATORIA; nunca recomendacao automatica.
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
    105,   # NM Cupen (NORUEGA - copa nacional): 0/3 partidas com estatisticas
           # na auditoria 13/09/2026. Era mapeado por erro como "Eliteserien"
           # (Eliteserien real = 103, 28/28 com estatisticas -> classe B).
           # NM Cupen e uma copa de cobertura fraca -> classe E.
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
    # --- ETAPA 4 (13/09/2026) - correcoes baseadas em auditoria cirurgica ---
    848: "UEFA Conference League: 135/263 partidas encerradas com "
         "estatisticas (51%); 128 vazios em jogos ENCERRADOS (FT/AET/PEN) "
         "- nao e atraso, e ausencia estrutural. Reclassificada A->C. "
         "Validacao dinamica obrigatoria; nunca recomendacao automatica.",
    103: "Eliteserien (Noruega): 28/28 partidas com estatisticas (100%) na "
         "auditoria. NOTA: 105 (NM Cupen, copa norueguesa) era mapeado por "
         "erro como Eliteserien; 103 e a liga real. Adicionada a classe B "
         "somente na matriz de cobertura - NAO promove ao universo de "
         "analise (LIGAS_PRIORITARIAS) sem promocao explicita do operador.",
    105: "NM Cupen (Noruega - copa nacional): 0/3 partidas com estatisticas. "
         "Era mapeado por erro como 'Eliteserien'. Reclassificada para E. "
         "Eliteserien real = 103 (ver nota 103).",
    252: "Division Profesional Paraguai: 52/56 partidas com estatisticas "
         "(93%) em jogos encerrados. Reclassificada A->B (borda inferior "
         "da classe A, <95%). Permanece PERMITIDO com rotulo honesto.",
    3: "UEFA Europa League: 14/14 partidas com estatisticas; cobertura de "
       "dados SOLIDA. Porem odds pre-jogo NAO fornecidas pela fonte (flag "
       "odds=false no /leagues) - nao afeta analise de dados, apenas odds.",
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


# ======================================================================
# ETAPA 4 (13/09/2026) — MATRIZ DEFINITIVA DE COBERTURA DA API
# ----------------------------------------------------------------------
# Consolidacao operacional da auditoria cirurgica. Adiciona, SEM alterar
# a logica de elegibilidade ja existente (classe_estrutural / veredictos
# / filtragem por mercado), camadas de status para ODDS, LIVE/PRESSAO,
# BACKTEST e RESULTADO — para servir como filtro ANTES das chamadas caras
# e como relatorio oficial.
#
# REGRAS DA ETAPA 4 (verbatim do operador):
#   - PRECISAO > QUANTIDADE. Nao ampliar cobertura so para aumentar jogos.
#   - Nao invente cobertura; o que vale e o que a conta/ plano entrega.
#   - Estados por MERCADO (nao bloqueio global): se falta corners, GOALS
#     continua.
#   - LIVE PARCIAL nao e tratado como completo; None nunca vira zero.
#   - Odds LIVE nao disponivel na fonte -> nunca inventar.
#   - A matriz NAO promove nenhum mercado experimental para validado.
# ======================================================================

# --- Status de BACKTEST (FASE G) ---
BACKTEST_VIAVEL = "VIÁVEL"
BACKTEST_PARCIAL = "PARCIAL"
BACKTEST_INVIÁVEL = "INVIÁVEL"
BACKTEST_A_CONFIRMAR = "A CONFIRMAR"

# --- Status de LIVE / PRESSAO 5/10/15 (FASE C) ---
LIVE_COMPLETO = "LIVE COMPLETO"
LIVE_PARCIAL = "LIVE PARCIAL"
LIVE_INSUFICIENTE = "LIVE INSUFICIENTE"
LIVE_NAO_TESTADO = "LIVE NÃO TESTADO"

# --- Status de ODDS (FASE D) ---
ODDS_PERMITIDO = "PERMITIDO"
ODDS_INSUFICIENTE = "INSUFICIENTE"
ODDS_NAO_TESTADO = "NÃO TESTADO"
ODDS_LIVE_INDISPONIVEL = "INDISPONÍVEL NA FONTE"

# --- Mercado RESULTADO (FASE H) ---
MERCADO_RESULTADO = "RESULTADO"
# Espelha src/resultado.py:RISCO_STATUS_RESULTADO — a matriz de cobertura
# NUNCA promove RESULTADO para validado (FASE H).
STATUS_RESULTADO_EXPERIMENTAL = "EXPERIMENTAL EM OBSERVAÇÃO"
# Espelha src/live_pressure.py:PRESSURE_STATUS — pressao 5/10/15 continua
# EXPERIMENTAL / A CALIBRAR; a matriz nao cria threshold (FASE H).
STATUS_PRESSAO_EXPERIMENTAL = "EXPERIMENTAL / A CALIBRAR"

# Campos usados para classificar a completude LIVE (FASE C). Ausencia de
# qualquer campo importante mantem LIVE PARCIAL/INSUFICIENTE — None nunca
# vira zero.
CAMPOS_PRESSAO = (
    "minuto",
    "placar",
    "Corner Kicks",
    "Total Shots",
    "Shots on Goal",
    "Blocked Shots",
    "Ball Possession",
)

# --- Odds PRE (FASE D) ---
# Ligas com flag odds=false no /leagues (confirmado pela auditoria): odds
# pre-jogo NAO fornecidas pela fonte. Nao afeta analise de DADOS, apenas
# odds -> OPORTUNIDADE ESTATISTICA sem odd real.
_ODDS_PRE_NEGADO_COBERTURA = frozenset({3, 848, 475, 624})

# Ligas com odds pre-jogo CONFIRMADAS na fonte (flag odds=true e/ou odds
# reais vistas no cache). Resto => NAO TESTADO (nao assume, nao inventa).
_ODDS_PRE_CONFIRMADO_TRUE = frozenset({
    2, 11, 39, 61, 71, 73, 76, 78, 82, 88, 94, 128, 130, 135, 140,
    203, 233, 239, 241, 253, 262, 479,
})

# --- LIVE / PRESSAO (FASE C) ---
# Ligas onde LIVE foi confirmado por observacao na auditoria (16 stats ao
# vivo verificadas). Usado para fundamentar LIVE COMPLETO/PARCIAL.
_LIVE_CONFIRMADO_OBSERVACAO = frozenset({
    2, 88, 307, 39, 140, 135, 78, 61, 94, 71, 128, 89, 204, 106, 113, 233,
})

# Ligas com cobertura LIVE explicitamente PARCIAL mesmo sendo classe A/B
# (validacao dinamica obrigatoria; nunca recomendacao automatica).
_LIVE_PARCIAL_OBRIGATORIO = frozenset({73, 848})

# Nomes canonicos para o relatorio oficial (fonte: auditoria 08-13/09/2026).
# Para IDs fora deste mapa, o relatorio usa f"Liga {id}" — honesto, sem
# inventar nome.
_NOME_LIGA = {
    71: "Brasileirão Série A",
    72: "Série B BR",
    73: "Copa do Brasil",
    2: "UEFA Champions League",
    3: "UEFA Europa League",
    848: "UEFA Conference League",
    39: "Premier League (Inglaterra)",
    140: "La Liga (Espanha)",
    135: "Serie A (Itália)",
    78: "Bundesliga (Alemanha)",
    61: "Ligue 1 (França)",
    88: "Eredivisie (Holanda)",
    94: "Primeira Liga (Portugal)",
    203: "Süper Lig (Turquia)",
    204: "1. Lig (Turquia)",
    106: "Ekstraklasa (Polônia)",
    113: "Allsvenskan (Suécia)",
    144: "Jupiler Pro League (Bélgica)",
    128: "Liga Profesional (Argentina)",
    262: "Liga MX (México)",
    307: "Saudi Pro League",
    281: "Primera Division (Uruguai)",
    239: "Primera A (Colômbia)",
    253: "Major League Soccer",
    242: "Serie A (Equador)",
    252: "Division Profesional (Paraguai)",
    13: "Copa Libertadores",
    11: "Copa Sudamericana",
    772: "Leagues Cup",
    89: "Eerste Divisie (Holanda 2ª)",
    475: "Paulistão A1",
    624: "Carioca 1",
    233: "Premier League (Egito)",
    114: "Superettan (Suécia 2ª)",
    141: "Segunda División (Espanha)",
    41: "League One (Inglaterra)",
    95: "Segunda Liga (Portugal)",
    136: "Série B (Itália)",
    137: "Coppa Italia",
    16: "CONCACAF Champions League",
    197: "Super League (Grécia)",
    344: "Liga 344 (1ª divisão, amostra pequena)",
    383: "Liga 383 (Israel, amostra pequena)",
    172: "Liga 172 (1ª divisão, amostra pequena)",
    119: "Liga 119 (1ª divisão, amostra pequena)",
    62: "Liga 62 (1ª divisão, amostra pequena)",
    103: "Eliteserien (Noruega)",
    105: "NM Cupen (Noruega - copa)",
    479: "Canadian Premier League",
    82: "Frauen Bundesliga",
    45: "FA Cup",
    134: "Torneo Federal A (Argentina)",
    75: "Série C BR",
    76: "Série D BR",
    129: "Primera Nacional (Argentina)",
    130: "Copa Argentina",
    241: "Copa Colombia",
    205: "2. Lig (Turquia)",
    219: "2. Liga (Áustria)",
    138: "Liga 138 (amostra pequena)",
    943: "Série C (Itália - amostra pequena)",
    667: "Amistosos de seleções",
    477: "Gaúcho",
    629: "Mineiro",
    606: "Paranaense",
    604: "Catarinense",
    627: "Paraense",
    612: "Copa do Nordeste",
    290: "Liga 290",
    673: "Liga 673",
    131: "Primera C MET (Argentina)",
    132: "Primera B MET (Argentina)",
    887: "Liga 887",
}


def _status_estrutural(
    league_id: int | None, mercado: str, modo: str,
) -> str:
    """Status estrutural (PERMITIDO/OBSERVACAO/BLOQUEADO) de uma
    combinacao (liga, mercado, modo), sem olhar o fixture."""
    classe, _ = classe_estrutural(league_id, None, mercado, modo)
    return _STATUS_POR_CLASSE.get(classe, STATUS_OBSERVACAO)


def status_odds_live() -> tuple[str, str]:
    """Odds LIVE: SEMPRE indisponiveis na fonte (auditoria 28/28 vazios
    em /odds/live). Nao inventa, nao converte ausencia em zero.

    Retorna (ODDS_LIVE_INDISPONIVEL, motivo). Espelha src/odds.py
    SEM_ODD_LIVE.
    """
    return (
        ODDS_LIVE_INDISPONIVEL,
        "auditoria 13/09/2026: /odds/live retornou 0/28 partidas com "
        "odds ao vivo na conta/plano atual. Odds LIVE nao disponiveis "
        "na fonte -> OPORTUNIDADE ESTATISTICA sem odd real; ausencia "
        "nunca vira zero.",
    )


def status_odds_pre(
    league_id: int | None, league_name: str | None = None,
) -> tuple[str, str]:
    """Odds PRE-jogo por liga (FASE D).

    Retorna (status, motivo):
      - PERMITIDO: odds confirmadas na fonte para a liga;
      - INSUFICIENTE: flag odds=false / odds nao fornecidas (nao afeta
        analise de dados, apenas odds);
      - NAO TESTADO: sem evidencia positiva nem negativa (nao assume).
    """
    if league_id is None:
        return (ODDS_NAO_TESTADO,
                "liga sem ID: odds pre nao testadas (nao assume cobertura)")
    if league_id in _ODDS_PRE_NEGADO_COBERTURA:
        nome = _NOME_LIGA.get(league_id, league_name or f"Liga {league_id}")
        return (ODDS_INSUFICIENTE,
                f"{nome}: odds pre-jogo NAO fornecidas pela fonte (flag "
                f"odds=false no /leagues, confirmado na auditoria). Nao "
                f"afeta analise de DADOS -> OPORTUNIDADE ESTATISTICA sem "
                f"odd real.")
    if league_id in _ODDS_PRE_CONFIRMADO_TRUE:
        nome = _NOME_LIGA.get(league_id, league_name or f"Liga {league_id}")
        return (ODDS_PERMITIDO,
                f"{nome}: odds pre-jogo confirmadas na fonte (14 "
                f"bookmakers, 183 mercados observados no cache).")
    return (ODDS_NAO_TESTADO,
            f"Liga {league_id}: odds pre-jogo nao testadas na auditoria "
            f"(nao assume existencia nem ausencia).")


def status_live_pressao(
    league_id: int | None, league_name: str | None = None,
) -> tuple[str, str, tuple[str, ...]]:
    """Classifica a cobertura LIVE para pressao 5/10/15 (FASE C).

    Usa minuto/placar/Corner Kicks/Total Shots/Shots on Goal/Blocked
    Shots/Ball Possession. LIVE PARCIAL nao e tratado como completo;
    campo importante ausente -> mantem explicito. Retorna (status,
    motivo, campos_confirmados).
    """
    if league_id is None:
        return (LIVE_NAO_TESTADO,
                "liga sem ID: cobertura live nao testada (nao assume)",
                ())
    base = _CLASSE_AUDITORIA.get(league_id)
    nome = _NOME_LIGA.get(league_id, league_name or f"Liga {league_id}")

    if base is None:
        return (LIVE_NAO_TESTADO,
                f"{nome}: sem evidencia live na auditoria -> NAO TESTADO "
                f"(nao assume, nao inventa).",
                ())

    if league_id in _LIVE_PARCIAL_OBRIGATORIO:
        # 73 (Copa do Brasil: 88/150 parcial) e 848 (Conference: 51%):
        # LIVE PARCIAL mesmo em classe A/C. Validacao dinamica obrigatoria.
        return (LIVE_PARCIAL,
                f"{nome}: cobertura live PARCIAL (auditoria). "
                f"Validacao dinamica obrigatoria; LIVE PARCIAL nao e "
                f"tratado como completo. Pressao 5/10/15 = "
                f"{STATUS_PRESSAO_EXPERIMENTAL}.",
                ("minuto", "placar", "Corner Kicks"))

    if base in ("A", "B"):
        campos_full = CAMPOS_PRESSAO
        obs = " (live verificado na auditoria)" if (
            league_id in _LIVE_CONFIRMADO_OBSERVACAO) else ""
        return (LIVE_COMPLETO,
                f"{nome}: classe {base}{obs} -> entrega live confirmada "
                f"(16 tipos ao vivo). Pressao 5/10/15 = "
                f"{STATUS_PRESSAO_EXPERIMENTAL} (matriz nao cria threshold).",
                campos_full)

    if base == "C":
        return (LIVE_PARCIAL,
                f"{nome}: classe C -> cobertura live PARCIAL (~50% das "
                f"partidas com estatisticas). LIVE PARCIAL nao e tratado "
                f"como completo; campo ausente mantem explicito. Pressao "
                f"5/10/15 = {STATUS_PRESSAO_EXPERIMENTAL}.",
                ("minuto", "placar", "Corner Kicks"))

    # base == "E": sem estatisticas de partida na fonte
    return (LIVE_INSUFICIENTE,
            f"{nome}: classe E -> zero estatisticas live na fonte. "
            f"Pressao 5/10/15 = {STATUS_PRESSAO_EXPERIMENTAL} mas SEM "
            f"dado para calcular.",
            ())


def status_backtest(
    league_id: int | None, league_name: str | None = None,
) -> tuple[str, str]:
    """Status de viabilidade de BACKTEST por liga (FASE G).

    Considera: qty de fixtures, profundidade de historico, estatisticas,
    corners/cards/gols/placar, odds historicas reais. NAO executa
    backtest; somente prepara a matriz.
    """
    if league_id is None:
        return (BACKTEST_A_CONFIRMAR,
                "liga sem ID: backtest a confirmar (nao assume amostra)")
    base = _CLASSE_AUDITORIA.get(league_id)
    nome = _NOME_LIGA.get(league_id, league_name or f"Liga {league_id}")

    if base is None:
        return (BACKTEST_A_CONFIRMAR,
                f"{nome}: sem evidencia na auditoria -> backtest A "
                f"CONFIRMAR (nao assume amostra).")
    if league_id in _LIVE_PARCIAL_OBRIGATORIO:
        return (BACKTEST_PARCIAL,
                f"{nome}: backtest PARCIAL - cobertura de estatisticas "
                f"parcial ({'88/150' if league_id == 73 else '51%'}); "
                f"jogos sem estatisticas devem ser EXCLUIDOS da amostra "
                f"(nunca zerados). Odds historicas reais limitadas.")
    if base == "A":
        return (BACKTEST_VIAVEL,
                f"{nome}: classe A -> amostra ampla e estatisticas em "
                f">=95% dos jogos; backtest VIÁVEL (fixtures suficientes, "
                f"historico profundo, estatisticas completas). Odds pre "
                f"historicas disponiveis quando status_odds_pre=PERMITIDO.")
    if base == "B":
        return (BACKTEST_A_CONFIRMAR,
                f"{nome}: classe B -> 100% na amostra pequena; backtest A "
                f"CONFIRMAR (amostra pode ser curta para conclusao "
                f"estatistica robusta).")
    if base == "C":
        return (BACKTEST_PARCIAL,
                f"{nome}: classe C -> backtest PARCIAL (~50% dos jogos "
                f"com estatisticas); jogos sem estatisticas EXCLUIDOS "
                f"da amostra, nunca zerados. Conclusao limitada.")
    # base == "E"
    return (BACKTEST_INVIÁVEL,
            f"{nome}: classe E -> zero estatisticas de partida na fonte; "
            f"backtest INVIÁVEL para corners/cards/estatisticas (so "
            f"placares/gols via historico de resultados).")


def status_resultado(
    league_id: int | None, league_name: str | None = None,
    modo: str = MODO_PRE_GAME,
) -> tuple[str, str]:
    """Familia RESULTADO (FASE H): segue o veredicto de GOALS (mesmo
    dado base: placar / projetacao de gols), MAS permanece EXPERIMENTAL
    EM OBSERVAÇÃO. A matriz de cobertura NUNCA promove um mercado
    experimental para validado.

    Retorna (status_cobertura, status_validacao).
    """
    cov = _status_estrutural(league_id, MERCADO_GOALS, modo)
    nome = _NOME_LIGA.get(league_id, league_name or
                          (f"Liga {league_id}" if league_id else "liga"))
    return (cov,
            f"{nome}: RESULTADO segue cobertura de GOALS ({cov} em "
            f"{modo}), porem permanece {STATUS_RESULTADO_EXPERIMENTAL} "
            f"- validacao estatistica pendente. A matriz nao promove "
            f"mercado experimental para validado.")


# ----------------------------------------------------------------------
# Linha oficial da matriz (FASE I) — 16 colunas exigidas
# ----------------------------------------------------------------------
@dataclass
class LinhaMatrizOficial:
    competicao: str
    id_liga: int | None
    status_geral: str
    goals_pre: str
    goals_live: str
    corners_pre: str
    corners_live: str
    cards_pre: str
    cards_live: str
    resultado_pre: str
    resultado_live: str
    pressao_live: str
    odds_pre: str
    odds_live: str
    backtest: str
    motivo_observacao: str


def _status_geral_rollup(statuses: list[str]) -> str:
    """Rollup HONESTO do status geral por mercado (nao bloqueio global).
    STATUS GERAL e apenas um rotulo de resumo; o filtro real e por
    mercado (filtrar_avaliacoes_por_cobertura)."""
    if not statuses:
        return "NÃO TESTADO"
    if all(s == STATUS_PERMITIDO for s in statuses):
        return STATUS_PERMITIDO
    if all(s == STATUS_BLOQUEADO for s in statuses):
        return STATUS_BLOQUEADO
    tem_permitido = any(s == STATUS_PERMITIDO for s in statuses)
    tem_bloqueado = any(s == STATUS_BLOQUEADO for s in statuses)
    if tem_permitido and tem_bloqueado:
        return "MISTO (PERMITIDO+BLOQUEADO)"
    if tem_bloqueado:
        return "MISTO (OBSERVAÇÃO+BLOQUEADO)"
    return STATUS_OBSERVACAO  # so PERMITIDO+OBSERVACAO


def relatorio_matriz_oficial() -> "list[LinhaMatrizOficial]":
    """Gera a matriz oficial completa (FASE I), uma linha por competicao
    da auditoria, com as 16 colunas exigidas.

    Filtro ANTES das chamadas caras (FASE F): o consumer usa esta matriz
    para decidir se consulta /fixtures/statistics, /odds, etc. por
    COMPETICAO x MERCADO x MODO. Nao descarta jogo inteiro quando apenas
    um mercado e insuficiente.
    """
    linhas: list[LinhaMatrizOficial] = []
    for league_id in sorted(_CLASSE_AUDITORIA):
        nome = _NOME_LIGA.get(league_id, f"Liga {league_id}")

        g_pre = _status_estrutural(league_id, MERCADO_GOALS, MODO_PRE_GAME)
        g_live = _status_estrutural(league_id, MERCADO_GOALS, MODO_LIVE)
        c_pre = _status_estrutural(league_id, MERCADO_CORNERS, MODO_PRE_GAME)
        c_live = _status_estrutural(league_id, MERCADO_CORNERS, MODO_LIVE)
        cd_pre = _status_estrutural(league_id, MERCADO_CARDS, MODO_PRE_GAME)
        cd_live = _status_estrutural(league_id, MERCADO_CARDS, MODO_LIVE)

        # RESULTADO segue GOALS, permanece EXPERIMENTAL (FASE H)
        r_pre_cov, _ = status_resultado(league_id, nome, MODO_PRE_GAME)
        r_live_cov, _ = status_resultado(league_id, nome, MODO_LIVE)
        r_pre = f"{r_pre_cov} · {STATUS_RESULTADO_EXPERIMENTAL}"
        r_live = f"{r_live_cov} · {STATUS_RESULTADO_EXPERIMENTAL}"

        live_st, _, _ = status_live_pressao(league_id, nome)
        pressao = f"{live_st} · {STATUS_PRESSAO_EXPERIMENTAL}"

        odds_pre_st, _ = status_odds_pre(league_id, nome)
        odds_live_st, _ = status_odds_live()

        bt_st, _ = status_backtest(league_id, nome)

        status_geral = _status_geral_rollup(
            [g_pre, g_live, c_pre, c_live, cd_pre, cd_live])

        nota = _NOTA_ESPECIAL.get(league_id)
        if nota is None:
            base = _CLASSE_AUDITORIA.get(league_id, "?")
            nota = f"classe {base} (auditoria 08-13/09/2026)"
        else:
            nota = f"classe {_CLASSE_AUDITORIA.get(league_id, '?')} - {nota}"

        linhas.append(LinhaMatrizOficial(
            competicao=nome,
            id_liga=league_id,
            status_geral=status_geral,
            goals_pre=g_pre,
            goals_live=g_live,
            corners_pre=c_pre,
            corners_live=c_live,
            cards_pre=cd_pre,
            cards_live=cd_live,
            resultado_pre=r_pre,
            resultado_live=r_live,
            pressao_live=pressao,
            odds_pre=odds_pre_st,
            odds_live=odds_live_st,
            backtest=bt_st,
            motivo_observacao=nota,
        ))
    return linhas