---
description: Análise ao vivo de um jogo em andamento
---

Analise um jogo ao vivo.

Execute: `python -m src.app aovivo "$ARGUMENTS"`
(argumento: "Time A x Time B" ou o ID da partida)

Apresente em português: minuto, placar, escanteios no momento, chutes,
posse e a projeção de ritmo até os 90 minutos. Informe que as estatísticas
ao vivo atualizam a cada minuto (cache de 60s) — se o usuário quiser o
estado mais recente dali a alguns minutos, rode o comando de novo.