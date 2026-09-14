# CORNER INTELLIGENCE

Plataforma de análise de escanteios com a **API-Football Pro**, operada
diretamente pelo **Claude Code** (sem interface web). Pergunte em linguagem
natural ("Analise Flamengo x Palmeiras") ou use os comandos `/hoje`, `/jogo`,
`/time`, `/ultimos20`, `/comparar`, `/h2h`, `/liga`, `/prejogo`, `/aovivo`.

## Setup

1. Instale as dependências:
   ```
   pip install -r requirements.txt
   ```
2. Coloque sua chave da API-Football no arquivo `.env` (raiz do projeto):
   ```
   API_KEY=sua_chave_aqui
   ```
3. Teste:
   ```
   python -m src.app hoje
   ```
   Para rodar os testes (cálculos e cache não usam rede):
   ```
   python -m pytest
   ```

## Estrutura

- `CLAUDE.md` — regras operacionais (o Claude Code segue automaticamente)
- `src/` — backend modular
  - `config.py` — configuração (lê `.env`, TTLs, janelas e linhas de over)
  - `api_client.py` — cliente HTTP da API-Football v3 (erros amigáveis, nunca loga a chave)
  - `cache.py` — cache SQLite (`data/corner_intelligence.db`)
  - `fixtures.py` — jogos: últimos N, hoje, ao vivo, H2H, por ID
  - `match_stats.py` — estatísticas por partida (escanteios, chutes, cartões, posse)
  - `stats.py` — cálculos: janelas 5/10/20, média, mediana, desvio, over 7.5–11.5, tendência, casa/fora, xCorners
  - `h2h.py` — confronto direto (amostra exclusiva entre os dois times, máx. 50)
  - `resolver.py` — resolução de nomes de times/ligas (sem IDs; ambiguidade pergunta ao usuário)
  - `analysis.py` — análises: time, comparar, H2H, hoje, liga, pré-jogo, ao vivo, pressão ofensiva
  - `report.py` — formatação das saídas em português
  - `app.py` — CLI (`python -m src.app <comando>`)
- `.claude/commands/` — comandos do Claude Code
- `tests/` — testes (pytest)
- `logs/corner_intelligence.log` — log técnico

## Limitação conhecida (honestidade de dados)

A API-Football v3 fornece estatísticas apenas do **jogo completo**; não há
escanteios por 1º/2º tempo. O sistema informa "dado não disponível" e nunca
inventa valores. A estrutura já possui os campos para quando existir.