---
name: automation-design
description: Evalúa trabajo repetible y recomienda el flujo, equipo o programación segura más pequeña con aprobaciones explícitas.
source: worker
---

# Diseño de automatización

Antes de recomendar automatización, mapea el disparador, entradas, salida, decisiones,
fallos, coste y condición de parada. Elige el mecanismo útil más pequeño: trabajo directo,
especialista de solo lectura, worker duradero o programación. Explica qué decisiones deben
seguir requiriendo aprobación humana.

Trata conexiones, credenciales, horarios y escrituras externas como dependencias hasta
verificarlas. No afirmes que existe una integración ni crees automatizaciones sin aprobación.
Registra decisiones y evidencias importantes, sin ruido periódico de estado.
