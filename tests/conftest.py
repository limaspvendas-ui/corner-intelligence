"""Configuracao dos testes: garante import de src/ a partir da raiz.

ISOLAMENTO TOTAL DE PERSISTENCIA (regra da suite): nenhum teste pode
escrever nos bancos operacionais reais (data/corner_intelligence.db,
data/recomendacoes.db). Todos os caminhos de banco — cache, circuit
breaker, quota, request log, source_conflict, resolucao factual,
identidade validada, series live, snapshots de odds, registro de
recomendacoes — sao redirecionados por teste para um banco temporario
descartavel. O comportamento de PRODUCAO nao muda: apenas o caminho e
trocado durante o teste.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# atributos de caminho de banco que existem em src.config e que varios
# modulos importam no nivel de modulo (bypass do patch em src.config)
_CAMPOS_DB = ("DB_PATH", "REGISTRY_DB_PATH")

_PROVEDORES = (
    "api_football", "football_data_org", "apifootball_com",
    "the_odds_api", "five_dollar_football", "sportmonks",
    "statsbomb_open",
)


# Modulos que LEEM o banco operacional real SOMENTE-LEITURA durante os
# testes (por design, verificado por testes de regressao read-only):
# ingestao de odds do cache real (odds_coleta, mode=ro), auditoria
# multifonte e o proprio src.config — testes de auditoria leem
# src.config.DB_PATH em tempo de chamada esperando o banco REAL. Os
# fluxos que ESCREVEM lendo src.config em tempo de chamada (conflito
# factual em provider_router/resolucao_factual) sao isolados pelo
# fixture proprio do arquivo de testes de failover (_db_tmp).
_MODULOS_SOMENTE_LEITURA = ("src.config", "src.odds_coleta",
                            "src.auditoria_multifonte")


def _redirecionar_bancos(monkeypatch, novo_db: Path) -> None:
    """Aponta TODOS os modulos src.* que importaram um caminho de banco
    no nivel de modulo para `novo_db` (banco temporario do teste).
    src.config e os modulos somente-leitura nao sao tocados."""
    for nome in list(sys.modules):
        if not nome.startswith("src"):
            continue
        if nome in _MODULOS_SOMENTE_LEITURA:
            continue
        mod = sys.modules.get(nome)
        if mod is None:
            continue
        for campo in _CAMPOS_DB:
            if hasattr(mod, campo):
                monkeypatch.setattr(mod, campo, novo_db)


@pytest.fixture(autouse=True)
def _bancos_operacionais_isolados(tmp_path, monkeypatch):
    """Hermeticidade do estado de FONTES e de QUALQUER persistencia.

    Sem este isolamento, testes com clientes fake gravariam estado
    falso nos bancos REAIS da aplicacao (abrir/fechar circuit breaker,
    contaminar consumo_requisicoes_24h, cache, conflitos, registro) e
    os resultados ficariam dependentes da ordem de execucao. Cada
    teste recebe um banco limpo e descartavel.
    """
    from src import source_health

    db = tmp_path / "corner_intelligence_test.db"
    _redirecionar_bancos(monkeypatch, db)
    for provedor in _PROVEDORES:
        source_health.circuito_resetar(provedor)
    yield
    for provedor in _PROVEDORES:
        source_health.circuito_resetar(provedor)