# CORNER INTELLIGENCE — Regras Operacionais

Você está operando o **CORNER INTELLIGENCE**, uma plataforma de análise de escanteios
que usa a **API-Football Pro (v3, api-sports.io)**. O Claude Code é a interface
principal de operação: **não existe interface web, app ou dashboard**. Toda a operação
acontece aqui, com pedidos em linguagem natural ou comandos.

## Arquitetura

- Backend modular em `src/` (Python 3.10+), CLI de entrada: `python -m src.app <comando> [args]`
- Cache local em SQLite (`data/corner_intelligence.db`) para economizar requisições
- Credenciais no `.env` (nunca no código)
- Logs técnicos em `logs/corner_intelligence.log`
- Testes em `tests/` (cálculos + validação da API)

## Como atender pedidos em linguagem natural

Traduza o pedido para o comando correspondente e execute com a ferramenta de shell:

| Pedido do usuário | Comando |
|---|---|
| "Analise Flamengo x Palmeiras" / "Faça análise pré-jogo" | `python -m src.app prejogo "Flamengo x Palmeiras"` |
| "Mostre os últimos 20 jogos do Libertad" | `python -m src.app ultimos20 "Libertad"` |
| "Qual a média de escanteios desse confronto?" | `python -m src.app comparar "Flamengo" "Palmeiras"` |
| "Analise os jogos de hoje" | `python -m src.app hoje` |
| "Melhor tendência de over 9.5 escanteios?" | `python -m src.app hoje` e depois `prejogo` nos jogos candidatos |
| "Analise este jogo ao vivo" | `python -m src.app aovivo "Flamengo x Palmeiras"` |
| "Perfil de escanteios de um time" | `python -m src.app time "Palmeiras"` |
| "Média de escanteios de uma liga" | `python -m src.app liga "Serie A"` |
| "Detalhe do último confronto direto" | `python -m src.app jogo "Flamengo x Palmeiras"` |
| "Últimos N confrontos A x B" (até 50) | `python -m src.app h2h "A" "B" --n N` |

O usuário **nunca precisa saber IDs** de times, ligas ou fixtures — o backend resolve
nomes automaticamente. Argumentos entre aspas sempre que tiverem espaços.

Após executar, apresente a saída formatada em **português**, destacando os números
principais (médias, % de over, tendência). Não repita a saída bruta do comando
quando ela já estiver legível — destaque o que importa.

## Regras invioláveis

1. **NUNCA exponha a API_KEY** — em código, logs, respostas ou exemplos. Ela vive
   apenas no arquivo `.env`.
2. **NUNCA invente estatísticas.** Se a API não fornecer um dado, informe
   explicitamente "dado não disponível". Valores calculados vêm sempre do backend.
3. **H2H ≠ histórico individual.** "Últimos 10 confrontos Flamengo x Palmeiras"
   = as 10 partidas mais recentes disputadas **entre** Flamengo e Palmeiras
   (`h2h`), nunca os últimos 10 jogos de cada time separadamente (`comparar`).
   Limite máximo: 50 confrontos. Se houver menos confrontos disponíveis que o
   solicitado, informe **solicitados x encontrados** e **nunca complete a amostra
   artificialmente**. Apresentar por confronto: data, competição, fase, mandante,
   visitante, placar, escanteios, gols, finalizações, cartões, posse e
   1ºT/2ºT quando disponível.
4. **Nomes ambíguos**: se o backend responder `AMBIGUIDADE` com uma lista de
   candidatos, pergunte ao usuário qual deles antes de continuar. Não escolha sozinho.
5. **Estatísticas por tempo (1º/2º)**: o endpoint `/fixtures/statistics` aceita o
   parâmetro `half=true` e retorna, quando disponíveis para a partida, os blocos
   `statistics` (jogo completo), `statistics_1h` e `statistics_2h` (a API
   fornece esses dados a partir da temporada 2024). O backend usa `half=true`
   em toda busca. Exiba os tempos quando a API os retornar para aquela
   partida; se a resposta não trouxer o dado por tempo (ou trouxer um campo
   específico ausente, ex.: faltas por tempo), informe "dado não disponível na
   fonte". **Nunca afirme que a API-Football v3 não oferece estatísticas por
   tempo — essa afirmação é factualmente errada.**
6. **Cache**: as consultas são cacheadas automaticamente. Reanálises recentes são
   instantâneas e não gastam requisições. Jogos ao vivo usam TTL de 60 segundos.
7. **Erros**: mensagens do backend já são simples e em português — mostre-as
   como estão. Detalhes técnicos vão para `logs/corner_intelligence.log`.
8. **Contas de requisições**: o plano Pro tem limite por minuto. Prefira comandos
   já cacheados; evite refazer análises idênticas em sequência.
9. **xCorners** é uma heurística v1 (média ponderada de escanteios a favor/contra),
   claramente rotulada como estimativa — não a apresente como dado oficial.
10. **Saída padrão = dado, não opinião.** Por padrão, a resposta contém somente
    (1) DADOS DA API e (2) CÁLCULOS OBJETIVOS do Python solicitados pelo usuário.
    NUNCA inclua interpretação, projeção, tendência, previsão, palpite ou
    recomendação, e não os adicione na sua apresentação em linguagem natural.
    Gere ANÁLISE apenas quando o usuário pedir explicitamente ("analise",
    "faça uma análise", "dê um palpite", "faça uma projeção", "qual a tendência").
    Nesse caso, use a flag `--analise` no comando correspondente (projeção de
    ritmo no `aovivo`, tendência no `time`/`comparar`/`h2h`/`jogo`, tendência e
    xCorners no `prejogo`) — a saída padrão desses comandos não contém
    projeção, tendência nem estimativa.
11. **Média da liga (integridade).** "Média da Liga" só pode ser exibida quando:
    (a) a competição estiver identificada (com ID), (b) a temporada estiver
    identificada, (c) forem usadas TODAS as partidas finalizadas da competição
    nessa temporada (função `league_corner_average`), (d) partida sem estatística
    de escanteios na API for excluída e contabilizada — nunca transformada em
    zero — e (e) a resposta informar a quantidade de partidas válidas
    (válidas/encerradas/excluídas). **Nunca chame de "média da liga" uma média
    de últimos N jogos ou uma média calculada a partir de um único time.**
12. **Identidade dos times (IDs).** ID de time **nunca é assumido, memorizado
    ou hardcoded**: vem sempre da API-Football (`/teams`) ou do **próprio
    fixture conhecido** — quando houver um fixture, os IDs das equipes vêm
    obrigatoriamente dele (`fixture_teams` em `src/identity.py`). Antes de
    coletar estatísticas, o backend valida **ID + nome** (+ competição,
    quando aplicável); divergência interrompe a coleta com
    `IdentityDivergenceError` — **nunca continue a análise com outro clube
    por engano**. Somente associações já validadas são cacheadas (tabela
    `validated_teams`). A mesma identidade validada atravessa TODOS os
    módulos: escanteios, gols, cartões, finalizações, posse, H2H, mando,
    1ºT/2ºT e futuras análises asiáticas.

## Política permanente de universo e mercados (07/09/2026, `src/policy.py`)

Vale para **TODAS as novas análises** — teste/calibração E aposta real, pré-jogo E
ao vivo. Histórico já congelado permanece intacto.

1. **Universo único de análise.** Analisar SOMENTE competições fortes, conhecidas
   e com boa cobertura: `LIGAS_PRIORITARIAS` em `src/policy.py` (Premier League,
   La Liga, Serie A, Bundesliga, Ligue 1, Champions/Europa/Conference League,
   Brasileirão Série A, Primeira Liga de Portugal, Eredivisie, Süper Lig,
   principais da Argentina, Liga MX, principais da Colômbia, MLS — IDs resolvidos
   na API, nunca de memória). Outras competições só entram por **promoção explícita
   do usuário** em `LIGAS_EXTENSAO`. Excluir sempre: reservas, U20/21/23, amadores,
   desenvolvimento, divisões baixas/obscuras, cobertura fraca e **DADOS ≠ SIM**.
   Qualidade da amostra > quantidade.
2. **Mercados.** Para cada jogo elegível, avaliar TODOS os mercados com lógica
   **validada no projeto** (hoje: gols e escanteios over/under totais; Handicap
   Asiático e cartões entram somente quando validados) e **comparar entre si**:
   probabilidade, confiança, amostra, estabilidade histórica, casa/fora, benchmark
   da competição, momento, contradições, risco de outliers, cobertura e odd real
   quando disponível.
3. **Seleção.** Não escolher linha por probabilidade artificialmente alta; **penalizar
   linhas largas/conservadoras de utilidade prática desprezível** (odd justa ≈
   1/prob). Preferir por padrão **a MELHOR oportunidade por jogo** (score de
   política rotulado como heurística — nunca cálculo validado). Sem evidência
   forte ⇒ **REPROVAR o jogo**. Sem obrigação de recomendar.
4. **Implementação.** Pré-jogo: `prejogoop "A x B" --politica` (registra só a
   melhor, versão `prejogo-politica-1.0-observacao`). Ao vivo: `aovivoop` filtra o
   universo forte antes de registrar. Nada de cálculo validado muda: `src/policy.py`
   apenas seleciona quais jogos e qual linha.
5. **Odd real.** Mostrar odd e bookmaker quando disponíveis na API. Sem odd real,
   classificar como **OPORTUNIDADE ESTATÍSTICA** — nunca afirmar valor financeiro
   confirmado.

## Extensão futura (preparada, não ativa)

A estrutura já contempla: modelos estatísticos e ML (estenda `src/analysis.py`),
xCorners aprimorado, pressão ofensiva (chutes/posse de bola), fase da competição
e mata-mata (campo `round` dos fixtures), atualização em tempo real (TTL curto).
Não implemente interface web.