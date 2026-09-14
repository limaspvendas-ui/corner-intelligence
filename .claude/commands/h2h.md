---
description: Confronto direto - partidas ENTRE os dois times (H2H)
---

Busque o confronto direto (H2H) entre dois times.

Execute: `python -m src.app h2h "<time A>" "<time B>" --n 10`
(o usuário passa os nomes em $ARGUMENTS — divida em dois argumentos entre
aspas; ex.: "últimos 10 confrontos Flamengo x Palmeiras" → `h2h "Flamengo" "Palmeiras" --n 10`;
`--n` ajusta a quantidade, máximo 50)

REGRA CRÍTICA: H2H = apenas partidas disputadas ENTRE os dois times
(amostra exclusiva do confronto). Não é o histórico individual de cada
equipe — isso é o comando `comparar`. Nunca complete a amostra
artificialmente; se houver menos confrontos que o solicitado, informe
solicitados x encontrados.

Apresente por confronto: data, competição, fase, mandante, visitante,
placar, escanteios, gols, finalizações, cartões, posse e 1ºT/2ºT quando
disponível (na API-Football v3 os escanteios por tempo NÃO estão
disponíveis — informe isso quando relevante).