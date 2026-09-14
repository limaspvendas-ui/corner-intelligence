"""Configuracao central do Corner Intelligence.

Le a API_KEY do arquivo .env na raiz do projeto (nunca hardcoded).
Define caminhos (data/, logs/), TTLs do cache e constantes de calculo.
"""

import os
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")

# Credencial (secreta - existe apenas no .env)
API_KEY: str = os.getenv("API_KEY", "")

# URL base oficial da API-Football v3
BASE_URL: str = "https://v3.football.api-sports.io"

# Caminhos locais
DATA_DIR = PROJECT_ROOT / "data"
LOGS_DIR = PROJECT_ROOT / "logs"
DB_PATH = DATA_DIR / "corner_intelligence.db"
# Registro PERMANENTE de validacao das recomendacoes (separado do cache:
# o cache tem TTL; o registro nunca expira e nunca e sobrescrito)
REGISTRY_DB_PATH = DATA_DIR / "recomendacoes.db"
LOG_FILE = LOGS_DIR / "corner_intelligence.log"

# Parametros de analise
DEFAULT_LAST_N: int = 20          # jogos recentes analisados por padrao
STATS_WINDOWS: tuple = (5, 10, 20) # janelas: ultimos 5, 10, 20 jogos
OVER_LINES: tuple = (7.5, 8.5, 9.5, 10.5, 11.5)  # linhas de escanteios
DEFAULT_TIMEZONE: str = "America/Sao_Paulo"

# TTL do cache em segundos, por endpoint
CACHE_TTL = {
    "/fixtures/statistics": 7 * 86400,  # estatisticas de jogo encerrado sao imutaveis
    "/fixtures/headtohead": 3600,
    "/teams": 7 * 86400,
    "/teams/statistics": 3600,
    "/leagues": 7 * 86400,
    "/fixtures": 1800,
}
CACHE_TTL_DEFAULT: int = 86400  # 1 dia
CACHE_TTL_LIVE: int = 60        # jogos ao vivo atualizam a cada minuto


def require_api_key() -> str:
    """Retorna a API_KEY ou lanca UserFacingError com instrucao clara."""
    if not API_KEY:
        from src.exceptions import UserFacingError

        raise UserFacingError(
            "API_KEY nao configurada. Abra o arquivo .env na raiz do projeto "
            "e defina: API_KEY=sua_chave_aqui"
        )
    return API_KEY