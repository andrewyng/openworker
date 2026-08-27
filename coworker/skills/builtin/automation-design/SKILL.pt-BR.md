---
name: automation-design
description: Avalie trabalho repetível e recomende o menor fluxo, equipe ou agendamento seguro, com aprovações explícitas.
source: worker
---

# Design de automação

Mapeie gatilho, entradas, saída, pontos de decisão, falhas, custo e condição de parada
antes de recomendar uma automação. Escolha o menor mecanismo útil: trabalho direto,
especialista somente leitura, worker durável ou agendamento. Explique por que automatizar
e quais decisões continuam dependendo de aprovação humana.

Trate conexões, credenciais, agendas e escritas externas como dependências até serem
verificadas. Não afirme que uma integração existe nem crie automações sem aprovação.
Registre decisões e evidências relevantes, sem ruído periódico de status.
