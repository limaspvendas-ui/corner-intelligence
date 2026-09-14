"""Excecoes do Corner Intelligence.

UserFacingError: mensagem pronta para o usuario (simples, em portugues).
Erros tecnicos (rede, stack trace) vao para o log, nao para o usuario.
"""

from __future__ import annotations


class CornerIntelligenceError(Exception):
    """Erro base da plataforma."""


class UserFacingError(CornerIntelligenceError):
    """Erro cuja mensagem pode ser mostrada diretamente ao usuario."""


class NotFoundError(UserFacingError):
    """Recurso (time, liga, fixture) nao encontrado."""


class AmbiguousTeamError(UserFacingError):
    """Mais de um candidato para o nome informado.

    A mensagem lista os candidatos para que o usuario escolha.
    """

    def __init__(self, message: str, candidates: list[dict]) -> None:
        super().__init__(message)
        self.candidates = candidates


class DataUnavailableError(UserFacingError):
    """A API-Football nao fornece o dado pedido. Nunca inventar valor."""


class IdentityDivergenceError(UserFacingError):
    """Divergencia de identidade de um time (ID x nome x competicao).

    REGRA OBRIGATORIA: quando o ID resolvido nao corresponde ao time
    esperado, a coleta de estatisticas e INTERROMPIDA - nunca continua
    com outro clube por engano. A mensagem informa o esperado, o
    encontrado e onde a coleta parou.
    """