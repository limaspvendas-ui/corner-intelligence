---
description: Compara históricos individuais de dois times (não é H2H)
---

Compare os perfis de escanteios de dois times.

Execute: `python -m src.app comparar "<time A>" "<time B>"`
(o usuário passa os nomes em $ARGUMENTS — divida em dois argumentos
entre aspas; ex.: "Flamengo x Palmeiras" → `comparar "Flamengo" "Palmeiras"`)

IMPORTANTE: isto são duas amostras INDIVIDUAIS lado a lado. Se o usuário
quiser partidas ENTRE os dois times (confronto direto), use o backend
`h2h` em vez deste comando.

Apresente o resultado em português, destacando qual time tem maior média
de escanteios a favor e qual linha de over tem melhor frequência.