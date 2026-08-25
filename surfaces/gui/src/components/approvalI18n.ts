import type { Language } from "../i18n";

type ApprovalCopy = {
  allowOnce: string;
  deny: string;
  addSkills: string;
  notNow: string;
  allow: string;
  allowEveryTime: string;
  alwaysAllow: string;
  alwaysCommand: string;
  readonlyCommands: string;
  showLess: string;
  showAllLines: (count: number) => string;
  showFullMessage: string;
  preview: string;
  tool: string;
  approved: string;
  staysHere: string;
  overwrites: string;
  connectedService: string;
  leavesFor: (target: string) => string;
  searchProvider: string;
  currentProvider: (provider: string) => string;
  skillDestination: string;
  skillFooter: string;
  file: string;
  pngScreenshot: string;
  withMessage: string;
  alwaysAfterApproval: string;
  readOnly: string;
  titlePrefixes: Record<string, string>;
};

const copies: Record<Language, ApprovalCopy> = {
  "en-US": {
    allowOnce: "Allow once", deny: "Deny", addSkills: "Add to my skills", notNow: "Not now", allow: "Allow",
    allowEveryTime: "Allow every time", alwaysAllow: "Always allow", alwaysCommand: "Always allow this command",
    readonlyCommands: "Allow read-only commands", showLess: "show less", showAllLines: (count) => `show all ${count} lines`,
    showFullMessage: "show the full message", preview: "preview", tool: "Tool", approved: "Approved",
    staysHere: "stays on this computer", overwrites: "overwrites the existing file", connectedService: "acts on a connected service",
    leavesFor: (target) => `leaves this computer → ${target}`, searchProvider: "Queries go to your configured search provider",
    currentProvider: (provider) => `(currently: ${provider})`, skillDestination: "saves to Settings ▸ Skills",
    skillFooter: "Approving adds it to your skills on this computer — usable in every conversation from then on.",
    file: "file", pngScreenshot: "as a PNG screenshot", withMessage: "With the message",
    alwaysAfterApproval: "always allowed once you approve", readOnly: "read-only",
    titlePrefixes: {},
  },
  "pt-BR": {
    allowOnce: "Permitir uma vez", deny: "Negar", addSkills: "Adicionar às minhas habilidades", notNow: "Agora não", allow: "Permitir",
    allowEveryTime: "Permitir sempre", alwaysAllow: "Sempre permitir", alwaysCommand: "Sempre permitir este comando",
    readonlyCommands: "Permitir comandos somente leitura", showLess: "mostrar menos", showAllLines: (count) => `mostrar todas as ${count} linhas`,
    showFullMessage: "mostrar a mensagem completa", preview: "visualizar", tool: "Ferramenta", approved: "Aprovado",
    staysHere: "permanece neste computador", overwrites: "substitui o arquivo existente", connectedService: "atua em um serviço conectado",
    leavesFor: (target) => `sai deste computador → ${target}`, searchProvider: "As consultas vão para o seu provedor de pesquisa configurado",
    currentProvider: (provider) => `(atual: ${provider})`, skillDestination: "salva em Configurações ▸ Habilidades",
    skillFooter: "A aprovação adiciona a habilidade neste computador — ela poderá ser usada em todas as conversas a partir de agora.",
    file: "arquivo", pngScreenshot: "como captura de tela PNG", withMessage: "Com a mensagem",
    alwaysAfterApproval: "sempre permitido após a sua aprovação", readOnly: "somente leitura",
    titlePrefixes: {
      "Write ": "Gravar ", "Edit ": "Editar ", "Run a command": "Executar um comando", "Send a message": "Enviar uma mensagem",
      "Send a message to ": "Enviar uma mensagem para ", "Send a file": "Enviar um arquivo", "Send a file to ": "Enviar um arquivo para ",
      "Create the automation ": "Criar a automação ", "Create an automation": "Criar uma automação", "Add skill ": "Adicionar a habilidade ",
      "Add a skill to your skills": "Adicionar uma habilidade às suas habilidades", " to your skills": " às suas habilidades",
    },
  },
  "es-ES": {
    allowOnce: "Permitir una vez", deny: "Denegar", addSkills: "Añadir a mis habilidades", notNow: "Ahora no", allow: "Permitir",
    allowEveryTime: "Permitir siempre", alwaysAllow: "Permitir siempre", alwaysCommand: "Permitir siempre este comando",
    readonlyCommands: "Permitir comandos de solo lectura", showLess: "mostrar menos", showAllLines: (count) => `mostrar las ${count} líneas`,
    showFullMessage: "mostrar el mensaje completo", preview: "vista previa", tool: "Herramienta", approved: "Aprobado",
    staysHere: "permanece en este equipo", overwrites: "sobrescribe el archivo existente", connectedService: "actúa en un servicio conectado",
    leavesFor: (target) => `sale de este equipo → ${target}`, searchProvider: "Las consultas van a tu proveedor de búsqueda configurado",
    currentProvider: (provider) => `(actualmente: ${provider})`, skillDestination: "guarda en Configuración ▸ Habilidades",
    skillFooter: "Al aprobar, se añade la habilidad en este equipo y podrá usarse en todas las conversaciones a partir de ahora.",
    file: "archivo", pngScreenshot: "como captura de pantalla PNG", withMessage: "Con el mensaje",
    alwaysAfterApproval: "siempre permitido después de aprobar", readOnly: "solo lectura",
    titlePrefixes: {
      "Write ": "Guardar ", "Edit ": "Editar ", "Run a command": "Ejecutar un comando", "Send a message": "Enviar un mensaje",
      "Send a message to ": "Enviar un mensaje a ", "Send a file": "Enviar un archivo", "Send a file to ": "Enviar un archivo a ",
      "Create the automation ": "Crear la automatización ", "Create an automation": "Crear una automatización", "Add skill ": "Añadir la habilidad ",
      "Add a skill to your skills": "Añadir una habilidad a tus habilidades", " to your skills": " a tus habilidades",
    },
  },
};

export const approvalCopy = (language: Language): ApprovalCopy => copies[language] || copies["en-US"];

const toolVerbs: Record<Language, Record<string, string>> = {
  "en-US": { write_file: "Write a file", replace_in_file: "Edit a file", apply_patch: "Apply a patch", apply_unified_diff: "Apply a patch", run_shell: "Run a command", send_message: "Send a message", send_file: "Send a file" },
  "pt-BR": { write_file: "Gravar um arquivo", replace_in_file: "Editar um arquivo", apply_patch: "Aplicar uma alteração", apply_unified_diff: "Aplicar uma alteração", run_shell: "Executar um comando", send_message: "Enviar uma mensagem", send_file: "Enviar um arquivo" },
  "es-ES": { write_file: "Guardar un archivo", replace_in_file: "Editar un archivo", apply_patch: "Aplicar un cambio", apply_unified_diff: "Aplicar un cambio", run_shell: "Ejecutar un comando", send_message: "Enviar un mensaje", send_file: "Enviar un archivo" },
};

export const approvalToolVerb = (name: string, language: Language) => toolVerbs[language]?.[name] || name;

export function localizedApprovalDecision(decision: string, language: Language) {
  const labels: Record<Language, Record<string, string>> = {
    "en-US": { once: "allowed once", deny: "denied", always_tool: "always allowed", always_command: "command always allowed", always_domain: "domain always allowed", always_task: "always allowed for this automation", readonly_session: "read-only commands always allowed" },
    "pt-BR": { once: "permitido uma vez", deny: "negado", always_tool: "sempre permitido", always_command: "comando sempre permitido", always_domain: "domínio sempre permitido", always_task: "sempre permitido para esta automação", readonly_session: "comandos somente leitura sempre permitidos" },
    "es-ES": { once: "permitido una vez", deny: "denegado", always_tool: "siempre permitido", always_command: "comando siempre permitido", always_domain: "dominio siempre permitido", always_task: "siempre permitido para esta automatización", readonly_session: "comandos de solo lectura siempre permitidos" },
  };
  return labels[language]?.[decision] || decision.replace(/_/g, " ");
}

export function standingGrantTitle(language: Language, tool: string, target: string, task: string) {
  if (language === "pt-BR") return `Sempre permitir ${tool} → ${target} para “${task}” — revogue a qualquer momento na página Automações`;
  if (language === "es-ES") return `Permitir siempre ${tool} → ${target} para “${task}” — revócalo en cualquier momento en la página Automatizaciones`;
  return `Always allow ${tool} → ${target} for “${task}” — revoke any time on its Automations page`;
}

export function localizedApprovalLine(line: { pre: string; post?: string }, language: Language) {
  const copy = approvalCopy(language);
  const translate = (value: string) => {
    if (copy.titlePrefixes[value]) return copy.titlePrefixes[value];
    if (value.startsWith("Use ")) {
      return language === "pt-BR" ? `Usar ${value.slice(4)}` : language === "es-ES" ? `Usar ${value.slice(4)}` : value;
    }
    return value;
  };
  return { pre: translate(line.pre), post: line.post ? translate(line.post) : line.post };
}
