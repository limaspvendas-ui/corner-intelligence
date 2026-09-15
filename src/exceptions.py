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


# ----------------------------------------------------------------------
# Erros estruturados de FONTE (failover multifonte). Subclasses de
# UserFacingError para manter compatibilidade com handlers existentes.
# Nunca escondem o erro; nunca o convertem em "zero oportunidades".
# ----------------------------------------------------------------------
class SourceError(UserFacingError):
    """Erro estruturado de fonte de dados (classe base).

    `code` alimenta o campo `erro_codigo` da API/MCP; `fonte` informa qual
    provedor falhou; `motivo` documenta o gatilho (QUOTA_EXHAUSTED,
    RATE_LIMIT, TIMEOUT, CONNECTION_ERROR, HTTP_5XX, CREDENCIAL, ...).
    """

    code = "SOURCE_ERROR"
    fonte = "api_football"

    def __init__(self, message: str, *, motivo: str = "", fonte: str = "") -> None:
        super().__init__(message)
        self.motivo = motivo
        if fonte:
            self.fonte = fonte


class SourceQuotaExhaustedError(SourceError):
    """Cota diaria da fonte esgotada (ex.: plano PRO 7.500/dia)."""

    code = "SOURCE_QUOTA_EXHAUSTED"
    motivo_default = "QUOTA_EXHAUSTED"


class SourceRateLimitError(SourceError):
    """Limite por minuto da fonte atingido (HTTP 429)."""

    code = "SOURCE_RATE_LIMIT"
    motivo_default = "RATE_LIMIT"


class SourceTimeoutError(SourceError):
    """A fonte demorou demais para responder."""

    code = "SOURCE_TIMEOUT"
    motivo_default = "TIMEOUT"


class SourceUnavailableError(SourceError):
    """Fonte tecnica/indisponivel OU nenhuma fonte capaz para o dado.

    NUNCA significa "zero jogos"/"sem oportunidades": significa que o
    dado NAO ESTA DISPONIVEL por falha de fonte. O chamador deve
    reportar NAO DISPONIVEL, nunca converter em resultado vazio valido.
    """

    code = "SOURCE_UNAVAILABLE"
    motivo_default = "INDISPONIBILIDADE_TECNICA"


class SourceDataConflictError(SourceError):
    """Conflito factual entre fontes (ex.: 5 vs 6 escanteios).

    REGRA: conflito factual NUNCA e resolvido silenciosamente — ambas as
    versoes sao preservadas (tabela source_conflict) e o erro sobe
    estruturado. Prioridade automatica so existe para INDISPONIBILIDADE,
    jamais para mascarar divergencia factual.
    """

    code = "SOURCE_DATA_CONFLICT"
    motivo_default = "CONFLITO_DE_FONTE"