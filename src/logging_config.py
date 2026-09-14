"""Logging tecnico do Corner Intelligence.

Regra: detalhes tecnicos (stack traces, falhas HTTP) vao para
logs/corner_intelligence.log; o usuario recebe apenas mensagens simples
via UserFacingError / saida do CLI.
"""

import logging
from logging.handlers import RotatingFileHandler

from src.config import LOG_FILE, LOGS_DIR

LOGGER_NAME = "corner_intelligence"


def setup_logging() -> logging.Logger:
    """Configura o logger raiz da plataforma (idempotente)."""
    logger = logging.getLogger(LOGGER_NAME)
    if logger.handlers:  # ja configurado
        return logger

    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    logger.setLevel(logging.DEBUG)

    handler = RotatingFileHandler(
        LOG_FILE, maxBytes=2_000_000, backupCount=3, encoding="utf-8"
    )
    handler.setLevel(logging.DEBUG)
    handler.setFormatter(
        logging.Formatter(
            "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
        )
    )
    logger.addHandler(handler)
    return logger


def get_logger() -> logging.Logger:
    return setup_logging()