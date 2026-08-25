---
name: skill-creator
description: Crie, melhore, teste, compare e instale com segurança habilidades do Worker quando o usuário pedir um fluxo reutilizável.
source: worker
---

# Criador de habilidades

Ajude o usuário a criar ou melhorar uma habilidade do Worker. Reaproveite os fatos relevantes já informados na conversa e pergunte apenas pelas decisões que faltam: resultado esperado, situações de uso, entradas, saídas, limites e como o sucesso será avaliado.

Crie instruções concisas com `name` e `description` claros. Use `references/` para detalhes condicionais, `scripts/` para trabalho repetitivo determinístico e `assets/` apenas quando ajudarem de fato. Não copie textos de habilidades de terceiros; escreva instruções originais para o Worker.

Antes da instalação, use `stage_skill_draft` para guardar a candidata e dois ou três testes realistas. Explique os testes em linguagem simples. Use `run_skill_evals` somente depois da aprovação do usuário para o uso do modelo. Revise a comparação, as verificações objetivas e o feedback com `read_skill_eval`; ajuste o rascunho quando o feedback justificar.

As avaliações são isoladas. Não prometa acesso a conectores, rede, navegador, memória pessoal, comandos de shell ou pastas fora das entradas de teste. Se a habilidade exigir esses recursos, marque o caso para verificação manual.

Quando o usuário aceitar a versão final, chame `save_skill`. Ele continua sendo a etapa obrigatória de aprovação para adicionar ou atualizar a habilidade instalada. Nunca instale rascunhos diretamente.
