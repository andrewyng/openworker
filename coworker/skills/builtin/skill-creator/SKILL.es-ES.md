---
name: skill-creator
description: Crea, mejora, prueba, compara e instala de forma segura habilidades de Worker cuando el usuario solicite un flujo reutilizable.
source: worker
---

# Creador de habilidades

Ayuda al usuario a crear o mejorar una habilidad de Worker. Reutiliza los datos relevantes ya presentes en la conversación y pregunta solo por las decisiones faltantes: resultado, situaciones de uso, entradas, salidas, límites y criterios de éxito.

Redacta instrucciones concisas con `name` y `description` claros. Usa `references/` para detalles condicionales, `scripts/` para trabajo repetible y determinista, y `assets/` solo cuando aporten valor. No copies textos de habilidades de terceros; escribe instrucciones originales para Worker.

Antes de instalar, usa `stage_skill_draft` para guardar la candidata y dos o tres pruebas realistas. Explica las pruebas en lenguaje sencillo. Usa `run_skill_evals` solo después de que el usuario apruebe el uso del modelo. Revisa la comparación, las verificaciones objetivas y los comentarios con `read_skill_eval`; ajusta el borrador cuando los comentarios lo justifiquen.

Las evaluaciones están aisladas. No prometas acceso a conectores, red, navegador, memoria personal, comandos de shell ni carpetas fuera de las entradas de prueba. Si la habilidad requiere esos recursos, marca el caso para verificación manual.

Cuando el usuario acepte la versión final, llama a `save_skill`. Sigue siendo el paso obligatorio de aprobación para añadir o actualizar la habilidad instalada. No instales borradores directamente.
