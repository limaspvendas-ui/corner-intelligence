"""Regressao do CASAMENTO EXATO linha analisada x odd (regra 08/09/2026).

A auditoria offline da varredura pre-jogo de 09/09/2026 comprovou que o
casamento por substring aceitava mercados da mesma familia com
periodo/escopo DIFERENTES (1o tempo, total do mandante/visitante, Home
Team Total, Yellow isolado) como se fossem o mercado FT de total do
jogo. Estes testes fixam a regra obrigatoria:

    odd so casa com a linha analisada quando coincidem EXATAMENTE:
    fixture + familia + periodo FT + escopo (TOTAL DO JOGO) +
    lado Over/Under + linha numerica.

Os payloads abaixo sao EXCERTOS REAIS dos snapshots /odds em cache
coletados em 08/09/2026 (fixture IDs, casas, bet IDs, nomes de
mercado, values e odds verdadeiros - nada inventado).

IDs estaveis do feed: Goals Over/Under=5, Corners Over Under=45,
Cards Over/Under=80 (verificados em todas as respostas em cache).
"""

from src.odds import (
    ODD_CARTOES_NAO_VALIDADA,
    _parse_response,
    casar_odd_ft,
    mercado_ft_total,
)
from src.odds import BetMarket


# ----------------------------------------------------------------------
# Helpers: excertos reais dos snapshots /odds (08/09/2026)
# ----------------------------------------------------------------------
def _odds(fixture_id, bookmakers):
    return _parse_response(fixture_id, {"update": "", "bookmakers": bookmakers})


def _book(name, update, bets):
    return {"name": name, "update": update, "bets": bets}


def _bet(bet_id, name, values):
    return {"id": bet_id, "name": name,
            "values": [{"value": v, "odd": str(o)} for v, o in values]}


# Moreirense x Benfica (fixture 1575469) - excerto real do snapshot
MOREIRENSE = _odds(1575469, [
    _book("10Bet", "2026-09-08T20:47:18+00:00", [
        _bet(77, "Total Corners (1st Half)", [("Over 4.5", "2.00")]),
    ]),
    _book("Betano", "2026-09-08T20:47:18+00:00", [
        _bet(45, "Corners Over Under", [("Over 4.5", "1.04")]),
    ]),
])

# Twente x Telstar (fixture 1552142) - excerto real do snapshot
TWENTE = _odds(1552142, [
    _book("10Bet", "2026-09-08T20:29:54+00:00", [
        _bet(77, "Total Corners (1st Half)", [("Over 5.5", "2.00")]),
    ]),
    _book("Betfair", "2026-09-08T20:29:54+00:00", [
        _bet(45, "Corners Over Under", [("Over 5.5", "1.01")]),
    ]),
])

# Estudiantes L.P. x Corinthians (fixture 1631506) - excerto real
ESTUDIANTES = _odds(1631506, [
    _book("10Bet", "2026-09-08T12:30:15+00:00", [
        _bet(57, "Home Corners Over/Under", [("Over 5.5", "1.95")]),
    ]),
    _book("Unibet", "2026-09-08T12:30:15+00:00", [
        _bet(45, "Corners Over Under", [("Over 5.5", "1.08")]),
    ]),
])

# Fortaleza EC x Avai (fixture 1520865) - excerto real do snapshot
FORTALEZA = _odds(1520865, [
    _book("Bet365", "2026-09-08T20:19:21+00:00", [
        _bet(57, "Home Corners Over/Under", [("Over 6.5", "1.83")]),
    ]),
    _book("Unibet", "2026-09-08T20:19:21+00:00", [
        _bet(45, "Corners Over Under", [("Over 6.5", "1.11")]),
    ]),
])

# Palmeiras x LDU de Quito - excerto real: SOMENTE Home Corners no
# snapshot (sem mercado FT de total do jogo para as linhas 5.5/6.5/7.5)
PALMEIRAS = _odds(5001, [
    _book("1xBet", "2026-09-08T12:30:15+00:00", [
        _bet(57, "Home Corners Over/Under",
             [("Over 5.5", "1.35"), ("Under 5.5", "2.95"),
              ("Over 7.5", "1.90"), ("Under 7.5", "1.80")]),
    ]),
    _book("Bet365", "2026-09-08T12:30:15+00:00", [
        _bet(57, "Home Corners Over/Under",
             [("Over 6.5", "1.67"), ("Under 6.5", "2.10")]),
    ]),
])

# Santos x Atletico-MG (fixture 1631510) - excerto real do snapshot
SANTOS = _odds(1631510, [
    _book("Bet365", "2026-09-08T20:05:22+00:00", [
        _bet(82, "Home Team Total Cards", [("Over 2.5", "2.20")]),
    ]),
    _book("Betano", "2026-09-08T20:05:22+00:00", [
        _bet(80, "Cards Over/Under", [("Over 2.5", "1.20")]),
    ]),
])

# Operario-PR x CRB (fixture 1520866) - excerto real: SOMENTE Home Team
# Total Cards (sem Cards Over/Under no snapshot)
OPERARIO = _odds(1520866, [
    _book("Bet365", "2026-09-08T16:17:18+00:00", [
        _bet(82, "Home Team Total Cards", [("Over 2.5", "2.00")]),
    ]),
])


# ----------------------------------------------------------------------
# 1. CASOS REAIS OBRIGATORIOS (antes errado -> depois exato)
# ----------------------------------------------------------------------
def test_moreirense_o45_esc_primeiro_tempo_nao_casa_ft_casa():
    """Antes: 2.00 de 'Total Corners (1st Half)'. Depois: FT exato 1.04."""
    cas = casar_odd_ft(MOREIRENSE, "escanteios", "Over", 4.5)
    assert cas is not None
    assert cas.odd == 1.04
    assert cas.bookmaker == "Betano"
    assert cas.mercado_feed == "Corners Over Under"
    assert cas.value_feed == "Over 4.5"
    assert cas.validada is True


def test_twente_o55_esc_primeiro_tempo_nao_casa_ft_casa():
    """Antes: 2.00 de 'Total Corners (1st Half)'. Depois: FT exato 1.01."""
    cas = casar_odd_ft(TWENTE, "escanteios", "Over", 5.5)
    assert cas is not None
    assert cas.odd == 1.01
    assert cas.bookmaker == "Betfair"
    assert cas.mercado_feed == "Corners Over Under"
    assert cas.validada is True


def test_estudiantes_o55_esc_home_corners_nao_casa_ft_casa():
    """Antes: 1.95 de 'Home Corners Over/Under'. Depois: FT exato 1.08."""
    cas = casar_odd_ft(ESTUDIANTES, "escanteios", "Over", 5.5)
    assert cas is not None
    assert cas.odd == 1.08
    assert cas.bookmaker == "Unibet"
    assert cas.mercado_feed == "Corners Over Under"
    assert cas.validada is True


def test_fortaleza_o65_esc_home_corners_nao_casa_ft_casa():
    """Antes: 1.83 de 'Home Corners Over/Under'. Depois: FT exato 1.11."""
    cas = casar_odd_ft(FORTALEZA, "escanteios", "Over", 6.5)
    assert cas is not None
    assert cas.odd == 1.11
    assert cas.bookmaker == "Unibet"
    assert cas.mercado_feed == "Corners Over Under"
    assert cas.validada is True


def test_palmeiras_somente_home_corners_odd_nao_disponivel():
    """Snapshot sem 'Corners Over Under' FT: nenhuma linha casa -
    ODD REAL NAO DISPONIVEL, nunca a odd de Home Corners."""
    for linha in (5.5, 6.5, 7.5):
        assert casar_odd_ft(PALMEIRAS, "escanteios", "Over", linha) is None
        assert casar_odd_ft(PALMEIRAS, "escanteios", "Under", linha) is None


def test_santos_o25_cartoes_liquidacao_nao_confirmada():
    """'Home Team Total Cards' NUNCA casa com total do jogo. O mercado
    FT exato existe no snapshot (1.20), mas cartoes sao devolvidos com
    validada=False: REGRA DE LIQUIDACAO NAO CONFIRMADA."""
    cas = casar_odd_ft(SANTOS, "cartoes", "Over", 2.5)
    assert cas is not None
    assert cas.odd == 1.20
    assert cas.mercado_feed == "Cards Over/Under"
    assert cas.bookmaker == "Betano"
    assert cas.validada is False
    assert ODD_CARTOES_NAO_VALIDADA in cas.motivo_nao_validada


def test_operario_o25_cartoes_sem_mercado_ft_nao_casa():
    """Somente 'Home Team Total Cards' no snapshot: nada casa com o
    total do jogo - ODD REAL NAO DISPONIVEL."""
    assert casar_odd_ft(OPERARIO, "cartoes", "Over", 2.5) is None


def test_cartoes_nunca_validados_para_operacional():
    """Regra: mesmo com mercado FT exato no feed, cartoes ficam NAO
    VALIDADOS ate a regra de liquidacao da casa ser confirmada."""
    for odds in (SANTOS, OPERARIO):
        cas = casar_odd_ft(odds, "cartoes", "Over", 2.5)
        if cas is not None:
            assert cas.validada is False


# ----------------------------------------------------------------------
# 2. REGRESSAO GENERIC - mercados que NUNCA casam com FT total do jogo
# ----------------------------------------------------------------------
ARMADILHAS = _odds(9999, [
    _book("10Bet", "2026-09-08T20:00:00+00:00", [
        _bet(6, "Goals Over/Under First Half", [("Over 2.5", "1.17")]),
        _bet(26, "Goals Over/Under - Second Half", [("Over 2.5", "1.25")]),
        _bet(77, "Total Corners (1st Half)", [("Over 9.5", "1.85")]),
        _bet(127, "Total Corners (2nd Half)", [("Over 9.5", "2.10")]),
        _bet(57, "Home Corners Over/Under", [("Over 9.5", "1.90")]),
        _bet(58, "Away Corners Over/Under", [("Over 9.5", "2.05")]),
        _bet(82, "Home Team Total Cards", [("Over 4.5", "1.95")]),
        _bet(83, "Away Team Total Cards", [("Over 4.5", "2.30")]),
        _bet(153, "Yellow Over/Under", [("Over 4.5", "1.80")]),
        _bet(105, "Home Team Total Goals(1st Half)", [("Over 1.5", "1.40")]),
        _bet(92, "Anytime Goal Scorer", [("Jogador X", "3.50")]),
    ]),
])


def test_goals_1st_half_nunca_casa_com_goals_ft():
    assert casar_odd_ft(ARMADILHAS, "gols", "Over", 2.5) is None


def test_goals_2nd_half_nunca_casa_com_goals_ft():
    assert casar_odd_ft(ARMADILHAS, "gols", "Over", 2.5) is None


def test_home_away_corners_nunca_casa_com_total_corners():
    assert casar_odd_ft(ARMADILHAS, "escanteios", "Over", 9.5) is None


def test_total_corners_1st_2nd_half_nunca_casa_com_ft():
    assert casar_odd_ft(ARMADILHAS, "escanteios", "Over", 9.5) is None


def test_home_away_cards_e_yellow_nunca_casa_com_total_cards():
    assert casar_odd_ft(ARMADILHAS, "cartoes", "Over", 4.5) is None


def test_mercado_de_jogador_nunca_casa():
    for fam in ("gols", "escanteios", "cartoes"):
        assert casar_odd_ft(ARMADILHAS, fam, "Over", 1.5) is None


def test_precedencia_ft_exato_sobre_armadilhas():
    """Com o mercado FT exato NO MESMO snapshot ao lado das armadilhas,
    o casamento devolve a odd do FT - nunca a da armadilha."""
    raw = {
        "update": "2026-09-08T20:00:00+00:00",
        "bookmakers": [
            _book("10Bet", "2026-09-08T20:00:00+00:00", [
                _bet(6, "Goals Over/Under First Half", [("Over 2.5", "1.17")]),
                _bet(77, "Total Corners (1st Half)", [("Over 9.5", "1.85")]),
            ]),
            _book("Marathonbet", "2026-09-08T20:00:00+00:00", [
                _bet(5, "Goals Over/Under", [("Over 2.5", "1.50")]),
                _bet(45, "Corners Over Under",
                     [("Over 9.5", "1.75"), ("Under 9.5", "2.05")]),
            ]),
        ],
    }
    odds = _parse_response(9998, raw)
    cas_g = casar_odd_ft(odds, "gols", "Over", 2.5)
    assert cas_g is not None and cas_g.odd == 1.50
    assert cas_g.mercado_feed == "Goals Over/Under"
    cas_c = casar_odd_ft(odds, "escanteios", "Over", 9.5)
    assert cas_c is not None and cas_c.odd == 1.75
    assert cas_c.bookmaker == "Marathonbet"
    # lado exato: Under 9.5 e a outra ponta do mesmo mercado FT
    cas_u = casar_odd_ft(odds, "escanteios", "Under", 9.5)
    assert cas_u is not None and cas_u.odd == 2.05


def test_linha_numerica_diferente_nao_casa():
    """Mercado FT certo, mas sem a linha pedida => NAO DISPONIVEL
    (nunca a linha vizinha)."""
    odds = _odds(9997, [
        _book("Marathonbet", "2026-09-08T20:00:00+00:00", [
            _bet(45, "Corners Over Under",
                 [("Over 9.5", "1.75"), ("Under 9.5", "2.05")]),
        ]),
    ])
    assert casar_odd_ft(odds, "escanteios", "Over", 10.5) is None
    assert casar_odd_ft(odds, "escanteios", "Over", 8.5) is None


def test_lado_diferente_nao_casa():
    odds = _odds(9996, [
        _book("10Bet", "2026-09-08T20:00:00+00:00", [
            _bet(5, "Goals Over/Under", [("Under 2.5", "1.60")]),
        ]),
    ])
    assert casar_odd_ft(odds, "gols", "Over", 2.5) is None


# ----------------------------------------------------------------------
# 3. ID estavel x nome canonico (fallback e defesa)
# ----------------------------------------------------------------------
def test_sem_id_nome_canonico_exato_casa():
    """Feed sem bet ID: correspondencia EXATA do nome canonico valido
    na auditoria casa normalmente."""
    odds = _odds(9995, [
        _book("Betano", "2026-09-08T20:00:00+00:00", [
            {"name": "Corners Over Under",
             "values": [{"value": "Over 9.5", "odd": "1.80"}]},
        ]),
    ])
    cas = casar_odd_ft(odds, "escanteios", "Over", 9.5)
    assert cas is not None and cas.odd == 1.80


def test_sem_id_nome_parecido_nunca_casa():
    """Sem ID e sem correspondencia EXATA (mesmo com barra a mais ou a
    menos) => NAO casa. Nunca 'parecido' substitui 'exato'."""
    odds = _odds(9994, [
        _book("Betano", "2026-09-08T20:00:00+00:00", [
            {"name": "Corners Over/Under",
             "values": [{"value": "Over 9.5", "odd": "1.80"}]},
            {"name": "Corner Over Under",
             "values": [{"value": "Over 9.5", "odd": "1.80"}]},
            {"name": "Goals Over/Under First Half",
             "values": [{"value": "Over 2.5", "odd": "1.17"}]},
        ]),
    ])
    assert casar_odd_ft(odds, "escanteios", "Over", 9.5) is None
    assert casar_odd_ft(odds, "gols", "Over", 2.5) is None


def test_id_certo_com_nome_de_escopo_errado_nunca_casa():
    """Defesa em profundidade: mesmo um bet ID correto (45) com nome
    trazendo marcador de periodo/escopo e rejeitado."""
    market = BetMarket(name="Home Corners Over/Under", id=45,
                       values=[])
    assert mercado_ft_total("escanteios", market) is False
    market2 = BetMarket(name="Corners Over Under (1st Half)", id=45,
                        values=[])
    assert mercado_ft_total("escanteios", market2) is False
    ok = BetMarket(name="Corners Over Under", id=45, values=[])
    assert mercado_ft_total("escanteios", ok) is True


def test_familia_desconhecida_e_odds_nula():
    assert casar_odd_ft(None, "escanteios", "Over", 9.5) is None
    assert casar_odd_ft(MOREIRENSE, "resultado", "Over", 9.5) is None
    # linha malformada: nunca interpreta
    assert casar_odd_ft(MOREIRENSE, "escanteios", "Over", "abc") is None


def test_shape_live_tambem_casa_por_id():
    """Shape ao vivo (odds agregadas): o bet ID estavel continua
    garantindo o casamento exato FT de total do jogo."""
    raw = {
        "update": "2026-09-08T21:14:00+00:00",
        "odds": [
            {"id": 45, "name": "Corners Over Under",
             "values": [{"value": "Over 9.5", "odd": "1.85"}]},
            {"id": 77, "name": "Total Corners (1st Half)",
             "values": [{"value": "Over 9.5", "odd": "3.40"}]},
        ],
    }
    odds = _parse_response(9993, raw)
    cas = casar_odd_ft(odds, "escanteios", "Over", 9.5)
    assert cas is not None
    assert cas.odd == 1.85
    assert cas.mercado_feed == "Corners Over Under"