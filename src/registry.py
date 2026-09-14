"""REGISTRO DE VALIDACAO DAS RECOMENDACOES (snapshot PERMANENTE).

Toda vez que a plataforma APROVA uma selecao (TOP 1/TOP 2 ao vivo), o
momento exato da recomendacao e CONGELADO aqui: fixture, data/hora
(America/Sao_Paulo), tipo (pre-jogo/ao vivo), competicao, times,
minuto/status, placar naquele momento, mercado, linha, equipe/lado,
odd real (somente se disponivel na fonte), bookmaker, probabilidade
estimada, confianca, tamanho da amostra, principais dados favoraveis,
contradicoes/riscos, timestamp do snapshot da API e versao da analise.

REGRA CRITICA - IMUTABILIDADE:
    - O registro original da recomendacao NUNCA e alterado
      retroativamente (mercado, linha, probabilidade, minuto, placar,
      dados utilizados, odd original...).
    - UPDATE e permitido SOMENTE nas colunas de resultado/liquidacao
      (placar_final, stats_finais, resultado_mercado,
      resultado_registrado_em, auditoria).
    - Um TRIGGER no SQLite rejeita (RAISE ABORT) qualquer UPDATE que
      toque os campos da previsao original - garantia no nivel do banco,
      nao apenas do codigo.
    - Correcoes posteriores geram NOTA DE AUDITORIA (JSON anexado, trilha
      completa preservada) ou um NOVO registro - nunca sobrescrita.

RESULTADO POSTERIOR (preenchido somente depois do encerramento):
    placar_final, stats_finais, resultado_mercado
    (GANHA / PERDIDA / DEVOLVIDA / MEIA VITORIA / MEIA DERROTA /
    NAO AVALIAVEL). A previsao original nunca e modificada depois de
    conhecido o resultado.

Este modulo NAO faz analise: apenas persiste dados JA CALCULADOS e
consulta. Nenhum valor e inventado: campo sem evidencia fica NULL.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from src.config import DATA_DIR, REGISTRY_DB_PATH
from src.exceptions import NotFoundError, UserFacingError
from src.live import now_brt

# Rotulos canonicos de liquidacao (exatamente os definidos na etapa)
RESULTADOS_VALIDOS = (
    "GANHA",
    "PERDIDA",
    "DEVOLVIDA",
    "MEIA VITÓRIA",
    "MEIA DERROTA",
    "NÃO AVALIÁVEL",
)
TIPOS_VALIDOS = ("prejogo", "live")

# Versao/tipo da analise que gerou a recomendacao
# live-op-2.3: a familia RESULTADO (1X2/DC/DNB/AH, leque validado) passa
# a concorrer no fluxo live com linhas canonicas liquidadaveis; sem odd
# live => OPORTUNIDADE ESTATISTICA. Rotulo apenas - nenhum calculo muda.
VERSAO_LIVE = "live-op-2.3-familia-resultado"
VERSAO_PREJOGO = "prejogo-1.0"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS recomendacoes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    fixture_id INTEGER NOT NULL,
    criado_em TEXT NOT NULL,
    tipo TEXT NOT NULL,
    versao_analise TEXT NOT NULL,
    competicao TEXT,
    mandante TEXT,
    visitante TEXT,
    minuto INTEGER,
    status TEXT,
    placar TEXT,
    mercado TEXT NOT NULL,
    linha TEXT NOT NULL,
    equipe_lado TEXT,
    odd REAL,
    bookmaker TEXT,
    probabilidade REAL NOT NULL,
    confianca REAL,
    amostra_n INTEGER,
    dados_favoraveis TEXT,
    contradicoes_riscos TEXT,
    snapshot_api_ts TEXT,
    placar_final TEXT,
    stats_finais TEXT,
    resultado_mercado TEXT,
    resultado_registrado_em TEXT,
    auditoria TEXT
);

-- REGRA CRITICA em nivel de banco: a previsao original e IMUTAVEL.
-- Qualquer UPDATE em campo da previsao => ABORT com mensagem clara.
CREATE TRIGGER IF NOT EXISTS trg_recomendacao_previsao_imutavel
BEFORE UPDATE ON recomendacoes
WHEN OLD.id IS NOT NEW.id
   OR OLD.fixture_id IS NOT NEW.fixture_id
   OR OLD.criado_em IS NOT NEW.criado_em
   OR OLD.tipo IS NOT NEW.tipo
   OR OLD.versao_analise IS NOT NEW.versao_analise
   OR OLD.competicao IS NOT NEW.competicao
   OR OLD.mandante IS NOT NEW.mandante
   OR OLD.visitante IS NOT NEW.visitante
   OR OLD.minuto IS NOT NEW.minuto
   OR OLD.status IS NOT NEW.status
   OR OLD.placar IS NOT NEW.placar
   OR OLD.mercado IS NOT NEW.mercado
   OR OLD.linha IS NOT NEW.linha
   OR OLD.equipe_lado IS NOT NEW.equipe_lado
   OR OLD.odd IS NOT NEW.odd
   OR OLD.bookmaker IS NOT NEW.bookmaker
   OR OLD.probabilidade IS NOT NEW.probabilidade
   OR OLD.confianca IS NOT NEW.confianca
   OR OLD.amostra_n IS NOT NEW.amostra_n
   OR OLD.dados_favoraveis IS NOT NEW.dados_favoraveis
   OR OLD.contradicoes_riscos IS NOT NEW.contradicoes_riscos
   OR OLD.snapshot_api_ts IS NOT NEW.snapshot_api_ts
BEGIN
    SELECT RAISE(ABORT,
        'REGISTRO IMUTAVEL: a previsao original da recomendacao nao pode ser alterada. Corrija com nota de auditoria ou novo registro.');
END;
"""

_COLUNAS = (
    "id", "fixture_id", "criado_em", "tipo", "versao_analise", "competicao",
    "mandante", "visitante", "minuto", "status", "placar", "mercado",
    "linha", "equipe_lado", "odd", "bookmaker", "probabilidade", "confianca",
    "amostra_n", "dados_favoraveis", "contradicoes_riscos",
    "snapshot_api_ts", "placar_final", "stats_finais", "resultado_mercado",
    "resultado_registrado_em", "auditoria",
)
_JSON_CAMPOS = ("dados_favoraveis", "contradicoes_riscos", "stats_finais",
                "auditoria")


def _agora() -> str:
    """America/Sao_Paulo, mesmo formato dos snapshots da plataforma."""
    return now_brt().strftime("%d/%m/%Y %H:%M:%S")


def _json_lista(valor: Any) -> str | None:
    """Lista -> JSON; None permanece None (nunca vira '[]' inventado)."""
    if valor is None:
        return None
    if isinstance(valor, str):
        return valor
    return json.dumps(valor, ensure_ascii=False)


def normalizar_resultado(rotulo: str) -> str:
    """Aceita grafias variadas e devolve o rotulo canonico."""
    texto = (rotulo or "").strip().upper()
    sem_acento = (
        texto.replace("Á", "A").replace("É", "E").replace("Í", "I")
        .replace("Ó", "O").replace("Ú", "U")
    )
    tabela = {
        "GANHA": "GANHA",
        "PERDIDA": "PERDIDA",
        "DEVOLVIDA": "DEVOLVIDA",
        "MEIA VITÓRIA": "MEIA VITÓRIA",
        "MEIA DERROTA": "MEIA DERROTA",
        "NÃO AVALIÁVEL": "NÃO AVALIÁVEL",
        "MEIA VITORIA": "MEIA VITÓRIA",
        "NAO AVALIAVEL": "NÃO AVALIÁVEL",
        "NAO AVALIÁVEL": "NÃO AVALIÁVEL",
        "NÃO AVALIAVEL": "NÃO AVALIÁVEL",
    }
    if sem_acento not in tabela:
        raise UserFacingError(
            "Resultado invalido. Use um destes: GANHA, PERDIDA, DEVOLVIDA, "
            "MEIA VITÓRIA, MEIA DERROTA, NÃO AVALIÁVEL."
        )
    return tabela[sem_acento]


def _normalizar_data(data: str | None) -> str | None:
    """Aceita DD/MM/YYYY ou YYYY-MM-DD -> prefixo DD/MM/YYYY."""
    if data is None:
        return None
    texto = data.strip()
    if len(texto) == 10 and texto[4] == "-":        # 2026-09-06
        from datetime import date

        try:
            d = date.fromisoformat(texto)
        except ValueError as exc:
            raise UserFacingError(
                f"Data invalida: '{data}'. Use DD/MM/YYYY."
            ) from exc
        return d.strftime("%d/%m/%Y")
    if len(texto) >= 8 and "/" in texto:            # 06/09/2026 ...
        return texto[:10]
    raise UserFacingError(f"Data invalida: '{data}'. Use DD/MM/YYYY.")


class RegistroRecomendacoes:
    """Armazem permanente e imutavel das recomendacoes aprovadas.

    Uso normal: RegistroRecomendacoes() => data/recomendacoes.db.
    Testes: RegistroRecomendacoes(db_path=tmp_path/"t.db").
    """

    def __init__(self, db_path: str | None = None) -> None:
        self.db_path = str(db_path or REGISTRY_DB_PATH)
        if self.db_path == str(REGISTRY_DB_PATH):
            DATA_DIR.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(_SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.db_path)

    # ------------------------------------------------------------------
    # REGISTRO (INSERT - congelamento do momento da aprovacao)
    # ------------------------------------------------------------------
    def registrar(
        self,
        *,
        fixture_id: int,
        tipo: str,
        mercado: str,
        linha: str,
        probabilidade: float,
        versao_analise: str,
        criado_em: str | None = None,
        competicao: str | None = None,
        mandante: str | None = None,
        visitante: str | None = None,
        minuto: int | None = None,
        status: str | None = None,
        placar: str | None = None,
        equipe_lado: str | None = None,
        odd: float | None = None,
        bookmaker: str | None = None,
        confianca: float | None = None,
        amostra_n: int | None = None,
        dados_favoraveis: list[str] | None = None,
        contradicoes_riscos: list[str] | None = None,
        snapshot_api_ts: str | None = None,
    ) -> int:
        """Congela UMA recomendacao aprovada. Campos sem evidencia ficam
        NULL - nunca zero, nunca inventado."""
        if tipo not in TIPOS_VALIDOS:
            raise UserFacingError(
                f"Tipo invalido: '{tipo}'. Use 'prejogo' ou 'live'."
            )
        with self._connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO recomendacoes (
                    fixture_id, criado_em, tipo, versao_analise, competicao,
                    mandante, visitante, minuto, status, placar, mercado,
                    linha, equipe_lado, odd, bookmaker, probabilidade,
                    confianca, amostra_n, dados_favoraveis,
                    contradicoes_riscos, snapshot_api_ts
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                          ?, ?, ?, ?, ?)
                """,
                (
                    fixture_id, criado_em or _agora(), tipo, versao_analise,
                    competicao, mandante, visitante, minuto, status, placar,
                    mercado, linha, equipe_lado, odd, bookmaker,
                    probabilidade, confianca, amostra_n,
                    _json_lista(dados_favoraveis),
                    _json_lista(contradicoes_riscos), snapshot_api_ts,
                ),
            )
            return int(cur.lastrowid)

    def registrar_de_avaliacao(
        self, av: Any, cand: Any, tipo: str = "live",
        versao_analise: str = VERSAO_LIVE,
    ) -> tuple[int, bool]:
        """Congela uma Avaliacao APROVADA (live) com seu Candidato.

        Dedupe: a MESMA recomendacao (mesmo fixture, mercado, linha,
        minuto, placar e probabilidade) nao gera segundo registro -
        retorna o id existente com novo=False.

        Retorna (id, criado_novo).
        """
        # dedupe - recomendacao antiga nunca e duplicada silenciosamente
        existente = self._busca_duplicada(av)
        if existente is not None:
            return existente, False

        # principais dados favoraveis / contradicoes: os MESMOS calculos
        # de exibicao do relatorio TOP 1/TOP 2 (reuso, nada novo aqui)
        from src.live_opportunity_report import _fatos_favor_contra

        snap = cand.snapshot
        historico = getattr(cand, "historico", {}) or {}
        ns = [
            v for v in (historico.get("n_home"), historico.get("n_away"))
            if v is not None
        ]
        amostra_n = min(ns) if ns else None
        fav, contra = _fatos_favor_contra(av, cand)
        contradicoes = [contra] + list(av.riscos or [])

        notas_auditoria: list[dict[str, Any]] = []
        if av.odd is not None and not av.odd.atual:
            notas_auditoria.append({
                "quando": _agora(),
                "acao": "registro da recomendacao",
                "nota": "odd anexada com timestamp fora da janela de "
                        "frescor; valor preservado como evidencia",
            })

        rec_id = self.registrar(
            fixture_id=av.fixture_id, tipo=tipo, mercado=av.mercado,
            linha=av.linha, probabilidade=av.prob,
            versao_analise=versao_analise, competicao=av.competicao,
            mandante=snap.home_team_name, visitante=snap.away_team_name,
            minuto=av.minuto, status=av.status, placar=av.placar,
            equipe_lado=(av.sustentacao or {}).get("equipe_lado"),
            odd=(av.odd.odd if av.odd is not None else None),
            bookmaker=(av.odd.bookmaker if av.odd is not None else None),
            confianca=av.confianca, amostra_n=amostra_n,
            dados_favoraveis=[fav], contradicoes_riscos=contradicoes,
            snapshot_api_ts=snap.collected_at,
        )
        if notas_auditoria:
            with self._connect() as conn:
                conn.execute(
                    "UPDATE recomendacoes SET auditoria = ? WHERE id = ?",
                    (json.dumps(notas_auditoria, ensure_ascii=False), rec_id),
                )
        return rec_id, True

    def _busca_duplicada(self, av: Any) -> int | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT id FROM recomendacoes
                WHERE fixture_id = ? AND mercado = ? AND linha = ?
                  AND minuto IS ? AND placar IS ? AND probabilidade = ?
                """,
                (av.fixture_id, av.mercado, av.linha, av.minuto, av.placar,
                 av.prob),
            ).fetchone()
        return int(row[0]) if row else None

    # ------------------------------------------------------------------
    # RESULTADO POSTERIOR (UPDATE restrito: SOMENTE liquidacao)
    # ------------------------------------------------------------------
    def registrar_resultado(
        self,
        rec_id: int,
        *,
        resultado_mercado: str,
        placar_final: str | None = None,
        stats_finais: Any = None,
        nota: str | None = None,
    ) -> dict[str, Any]:
        """Registra a liquidacao depois do encerramento. Atualiza SOMENTE
        os campos de resultado; a previsao original permanece intacta
        (garantia tambem pelo TRIGGER). Correcao de resultado preserva o
        valor anterior na trilha de auditoria."""
        resultado = normalizar_resultado(resultado_mercado)
        atual = self.obter(rec_id)  # garante existencia (NotFoundError)
        agora = _agora()

        auditoria = atual["auditoria"] or []
        entrada: dict[str, Any] = {
            "quando": agora,
            "acao": "resultado registrado",
            "resultado": resultado,
        }
        if placar_final is not None:
            entrada["placar_final"] = placar_final
        if nota:
            entrada["nota"] = nota
        # correcao (nunca sobrescrita silenciosa): valor anterior preservado
        if atual["resultado_mercado"] is not None:
            entrada["valor_anterior"] = atual["resultado_mercado"]
        auditoria.append(entrada)

        with self._connect() as conn:
            cur = conn.execute(
                """
                UPDATE recomendacoes
                SET placar_final = ?, stats_finais = ?, resultado_mercado = ?,
                    resultado_registrado_em = ?, auditoria = ?
                WHERE id = ?
                """,
                (
                    placar_final, _json_lista(stats_finais), resultado,
                    agora, json.dumps(auditoria, ensure_ascii=False), rec_id,
                ),
            )
            if cur.rowcount == 0:
                raise NotFoundError(
                    f"Recomendacao {rec_id} nao encontrada no registro."
                )
        return self.obter(rec_id)

    def anexar_auditoria(self, rec_id: int, nota: str) -> dict[str, Any]:
        """Correcao/comentario posterior: vira NOTA na trilha de auditoria
        - nunca altera a previsao original."""
        atual = self.obter(rec_id)
        auditoria = atual["auditoria"] or []
        auditoria.append({
            "quando": _agora(), "acao": "nota de auditoria", "nota": nota,
        })
        with self._connect() as conn:
            conn.execute(
                "UPDATE recomendacoes SET auditoria = ? WHERE id = ?",
                (json.dumps(auditoria, ensure_ascii=False), rec_id),
            )
        return self.obter(rec_id)

    # ------------------------------------------------------------------
    # CONSULTAS
    # ------------------------------------------------------------------
    def obter(self, rec_id: int) -> dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute(
                f"SELECT {', '.join(_COLUNAS)} FROM recomendacoes "
                "WHERE id = ?",
                (rec_id,),
            ).fetchone()
        if row is None:
            raise NotFoundError(
                f"Recomendacao {rec_id} nao encontrada no registro."
            )
        return self._para_dict(row)

    def listar(
        self,
        data: str | None = None,
        fixture_id: int | None = None,
        tipo: str | None = None,
    ) -> list[dict[str, Any]]:
        """Consulta por data (DD/MM/YYYY ou YYYY-MM-DD), fixture e/ou
        tipo. Sem filtros: tudo (historico completo)."""
        clausulas: list[str] = []
        params: list[Any] = []
        prefixo_data = _normalizar_data(data)
        if prefixo_data is not None:
            clausulas.append("criado_em LIKE ?")
            params.append(prefixo_data + "%")
        if fixture_id is not None:
            clausulas.append("fixture_id = ?")
            params.append(fixture_id)
        if tipo is not None:
            if tipo not in TIPOS_VALIDOS:
                raise UserFacingError(
                    f"Tipo invalido: '{tipo}'. Use 'prejogo' ou 'live'."
                )
            clausulas.append("tipo = ?")
            params.append(tipo)
        where = ("WHERE " + " AND ".join(clausulas)) if clausulas else ""
        # criado_en e texto DD/MM/YYYY: a ordem fiel e a do id (criacao)
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT {', '.join(_COLUNAS)} FROM recomendacoes "
                f"{where} ORDER BY id",
                params,
            ).fetchall()
        return [self._para_dict(r) for r in rows]

    @staticmethod
    def _para_dict(row: tuple) -> dict[str, Any]:
        rec = dict(zip(_COLUNAS, row))
        for campo in _JSON_CAMPOS:
            if rec.get(campo):
                try:
                    rec[campo] = json.loads(rec[campo])
                except (ValueError, TypeError):
                    rec[campo] = rec[campo]  # texto literal nao-JSON
            else:
                rec[campo] = None
        return rec