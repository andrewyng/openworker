import i18n from "i18next";
import { initReactI18next, useTranslation } from "react-i18next";
import { useEffect, useState } from "react";

export type Language = "pt-BR" | "en-US" | "es-ES";

const STORAGE_KEY = "openwork-language";
const FALLBACK_LANGUAGE: Language = "en-US";

export const translationResources = {
  "en-US": {
    translation: {
      settings: {
        title: "Settings",
        general: "General",
        generalDescription: "How Worker looks and behaves on this machine.",
        language: "Language",
        languageDescription: "Choose the language used throughout the Worker interface.",
        theme: "Theme",
        appearance: "Appearance",
        light: "Light",
        dark: "Dark",
        auto: "Auto",
        autoDescription: "Auto follows your system appearance.",
      },
    },
  },
  "pt-BR": {
    translation: {
      settings: {
        title: "Configurações",
        general: "Geral",
        generalDescription: "Como o Worker aparece e se comporta neste computador.",
        language: "Idioma",
        languageDescription: "Escolha o idioma usado em toda a interface do Worker.",
        theme: "Tema",
        appearance: "Aparência",
        light: "Claro",
        dark: "Escuro",
        auto: "Automático",
        autoDescription: "Automático segue a aparência do sistema.",
      },
    },
  },
  "es-ES": {
    translation: {
      settings: {
        title: "Configuración",
        general: "General",
        generalDescription: "Cómo se ve y se comporta Worker en este equipo.",
        language: "Idioma",
        languageDescription: "Elige el idioma usado en toda la interfaz de Worker.",
        theme: "Tema",
        appearance: "Apariencia",
        light: "Claro",
        dark: "Oscuro",
        auto: "Automático",
        autoDescription: "Automático sigue la apariencia del sistema.",
      },
    },
  },
} as const;

export const supportedLanguages: readonly Language[] = ["pt-BR", "en-US", "es-ES"];

/** Static UI phrases used by legacy surfaces while they are progressively moved to `t()`. */
const legacyPhrases: Record<Exclude<Language, "en-US">, Record<string, string>> = {
  "pt-BR": {
    "Models": "Modelos", "Context optimization": "Otimização de contexto", "Skills": "Habilidades", "Voice input": "Entrada de voz", "Memory": "Memória", "Coworkers": "Colegas de trabalho",
    "Save": "Salvar", "Cancel": "Cancelar", "Delete": "Excluir", "Remove": "Remover", "Edit": "Editar", "Close": "Fechar", "Back": "Voltar", "Continue": "Continuar", "Retry": "Tentar novamente", "Loading…": "Carregando…", "Loading...": "Carregando...", "Search": "Pesquisar", "New session": "Nova conversa", "New project": "Novo projeto", "Inbox": "Caixa de entrada", "Files": "Arquivos", "Browse": "Procurar", "Theme": "Tema", "Light": "Claro", "Dark": "Escuro", "Auto": "Automático",
    "General": "Geral", "Settings": "Configurações", "Language": "Idioma", "Appearance": "Aparência", "Connected": "Conectado", "Disconnected": "Desconectado", "Connect": "Conectar", "Disconnect": "Desconectar", "Install": "Instalar", "Installed": "Instalado", "Enable": "Ativar", "Disable": "Desativar", "Enabled": "Ativado", "Disabled": "Desativado", "Open": "Abrir", "Copy": "Copiar", "Copied": "Copiado", "Share": "Compartilhar", "Create": "Criar", "Update": "Atualizar", "Download": "Baixar", "Upload": "Enviar", "Send": "Enviar", "Stop": "Parar", "Start": "Iniciar", "Done": "Concluído", "Error": "Erro", "Warning": "Aviso", "Success": "Sucesso",
    "No results found": "Nenhum resultado encontrado", "No conversations yet": "Ainda não há conversas", "No files yet": "Ainda não há arquivos", "No items yet": "Ainda não há itens", "Show more": "Mostrar mais", "Show less": "Mostrar menos", "Working now": "Trabalhando agora", "Waiting for agent...": "Aguardando o agente...", "Dismiss": "Dispensar", "Attach": "Anexar", "Remove attachment": "Remover anexo", "Token usage": "Uso de tokens", "Mode": "Modo", "Send approvals to the Inbox": "Enviar aprovações para a caixa de entrada", "Run setup again": "Executar configuração novamente", "Check for updates": "Verificar atualizações", "Checking…": "Verificando…", "Verified": "Verificado", "Repair": "Reparar", "Ready": "Pronto", "Unsupported": "Não compatível", "Compatible": "Compatível",
    "Sidebar": "Barra lateral", "Conversations shown per coworker": "Conversas exibidas por colega", "Longer lists collapse behind “Show more”. Applies per coworker and per project.": "Listas maiores ficam ocultas em “Mostrar mais”. Aplica-se por colega e projeto.", "Composer": "Compositor", "Show the context window bar": "Mostrar a barra da janela de contexto", "Auto-approve (experimental)": "Aprovação automática (experimental)", "Enable Auto-approve mode": "Ativar modo de aprovação automática", "Trusted workspaces": "Espaços de trabalho confiáveis", "No workspaces are trusted.": "Nenhum espaço de trabalho é confiável.", "Setup & updates": "Configuração e atualizações", "Each conversation gets its own folder under this location.": "Cada conversa recebe sua própria pasta neste local.",
    "Ask the coworker…  (drop or paste files)": "Peça ao colega…  (solte ou cole arquivos)", "Ask anything…  (drop or paste files)": "Pergunte qualquer coisa…  (solte ou cole arquivos)", "Ask the coder to build, fix, or explain…  (drop or paste files)": "Peça ao programador para criar, corrigir ou explicar…  (solte ou cole arquivos)", "Connect a model": "Conectar um modelo", "No model connected — connect a model": "Nenhum modelo conectado — conecte um modelo", "Fetching the model list from the server": "Buscando a lista de modelos no servidor", "Configure Voice Input in Settings": "Configure a entrada de voz nas Configurações", "Start dictation": "Iniciar ditado", "Stop dictation": "Parar ditado", "Connect a model to send": "Conecte um modelo para enviar", "Send approvals to Inbox": "Enviar aprovações para a caixa de entrada", "Show sidebar": "Mostrar barra lateral", "Collapse sidebar": "Recolher barra lateral", "Dock sidebar": "Fixar barra lateral", "Show side panel": "Mostrar painel lateral", "Hide side panel": "Ocultar painel lateral", "Artifacts": "Artefatos",
  },
  "es-ES": {
    "Models": "Modelos", "Context optimization": "Optimización de contexto", "Skills": "Habilidades", "Voice input": "Entrada de voz", "Memory": "Memoria", "Coworkers": "Compañeros",
    "Save": "Guardar", "Cancel": "Cancelar", "Delete": "Eliminar", "Remove": "Quitar", "Edit": "Editar", "Close": "Cerrar", "Back": "Volver", "Continue": "Continuar", "Retry": "Reintentar", "Loading…": "Cargando…", "Loading...": "Cargando...", "Search": "Buscar", "New session": "Nueva conversación", "New project": "Nuevo proyecto", "Inbox": "Bandeja de entrada", "Files": "Archivos", "Browse": "Examinar", "Theme": "Tema", "Light": "Claro", "Dark": "Oscuro", "Auto": "Automático",
    "General": "General", "Settings": "Configuración", "Language": "Idioma", "Appearance": "Apariencia", "Connected": "Conectado", "Disconnected": "Desconectado", "Connect": "Conectar", "Disconnect": "Desconectar", "Install": "Instalar", "Installed": "Instalado", "Enable": "Activar", "Disable": "Desactivar", "Enabled": "Activado", "Disabled": "Desactivado", "Open": "Abrir", "Copy": "Copiar", "Copied": "Copiado", "Share": "Compartir", "Create": "Crear", "Update": "Actualizar", "Download": "Descargar", "Upload": "Subir", "Send": "Enviar", "Stop": "Detener", "Start": "Iniciar", "Done": "Listo", "Error": "Error", "Warning": "Aviso", "Success": "Correcto",
    "No results found": "No se encontraron resultados", "No conversations yet": "Aún no hay conversaciones", "No files yet": "Aún no hay archivos", "No items yet": "Aún no hay elementos", "Show more": "Mostrar más", "Show less": "Mostrar menos", "Working now": "Trabajando ahora", "Waiting for agent...": "Esperando al agente...", "Dismiss": "Descartar", "Attach": "Adjuntar", "Remove attachment": "Quitar archivo adjunto", "Token usage": "Uso de tokens", "Mode": "Modo", "Send approvals to the Inbox": "Enviar aprobaciones a la bandeja de entrada", "Run setup again": "Ejecutar configuración de nuevo", "Check for updates": "Buscar actualizaciones", "Checking…": "Buscando…", "Verified": "Verificado", "Repair": "Reparar", "Ready": "Listo", "Unsupported": "No compatible", "Compatible": "Compatible",
    "Sidebar": "Barra lateral", "Conversations shown per coworker": "Conversaciones mostradas por compañero", "Longer lists collapse behind “Show more”. Applies per coworker and per project.": "Las listas largas se ocultan en “Mostrar más”. Se aplica por compañero y proyecto.", "Composer": "Redactor", "Show the context window bar": "Mostrar la barra de ventana de contexto", "Auto-approve (experimental)": "Aprobación automática (experimental)", "Enable Auto-approve mode": "Activar modo de aprobación automática", "Trusted workspaces": "Espacios de trabajo de confianza", "No workspaces are trusted.": "No hay espacios de trabajo de confianza.", "Setup & updates": "Configuración y actualizaciones", "Each conversation gets its own folder under this location.": "Cada conversación tiene su propia carpeta en esta ubicación.",
    "Ask the coworker…  (drop or paste files)": "Pídele al compañero…  (suelta o pega archivos)", "Ask anything…  (drop or paste files)": "Pregunta cualquier cosa…  (suelta o pega archivos)", "Ask the coder to build, fix, or explain…  (drop or paste files)": "Pídele al programador que cree, corrija o explique…  (suelta o pega archivos)", "Connect a model": "Conectar un modelo", "No model connected — connect a model": "No hay ningún modelo conectado — conecta un modelo", "Fetching the model list from the server": "Obteniendo la lista de modelos del servidor", "Configure Voice Input in Settings": "Configura la entrada de voz en Configuración", "Start dictation": "Iniciar dictado", "Stop dictation": "Detener dictado", "Connect a model to send": "Conecta un modelo para enviar", "Send approvals to Inbox": "Enviar aprobaciones a la bandeja de entrada", "Show sidebar": "Mostrar barra lateral", "Collapse sidebar": "Contraer barra lateral", "Dock sidebar": "Acoplar barra lateral", "Show side panel": "Mostrar panel lateral", "Hide side panel": "Ocultar panel lateral", "Artifacts": "Artefactos",
  },
};

Object.assign(legacyPhrases["pt-BR"], {
  "Inbox cleanup": "Organização da caixa de entrada", "Review old email first, then organize it with your approval.": "Revise primeiro os e-mails antigos e depois organize-os com sua aprovação.", "The inbox to review and organize": "A caixa de entrada para revisar e organizar",
  "No skills yet —": "Ainda não há habilidades —", "teaches your worker its first one, like “prepare my Monday status report”.": "ensina a primeira habilidade ao seu agente, como “preparar meu relatório de status de segunda-feira”.",
  "Add skill": "Adicionar habilidade", "Write it myself": "Escrever eu mesmo", "A name, a description, and the instructions": "Um nome, uma descrição e as instruções", "Import a file": "Importar um arquivo", "A .zip or SKILL.md someone shared — you review before it installs": "Um arquivo .zip ou SKILL.md compartilhado — você revisa antes da instalação", "Create with OpenWorker": "Criar com o Worker", "Starts a conversation — the worker builds it and asks before adding it to your skills": "Inicia uma conversa — o Worker cria a habilidade e pergunta antes de adicioná-la às suas habilidades", "Upload a skill archive": "Enviar um arquivo de habilidade",
  "Channel where an Unattended session posts Approve/Deny buttons. Currently mirroring to": "Canal onde uma conversa autônoma publica botões de Aprovar/Negar. Atualmente espelhando para", "in-app Inbox only": "somente a caixa de entrada do aplicativo", "Set": "Definir", "clear": "limpar", "Session that handles DMs to the bot. With none, DMs park under Unrouted below.": "Conversa que atende mensagens diretas ao bot. Sem uma conversa definida, elas ficam em Sem roteamento abaixo.", "No session — park DMs": "Nenhuma conversa — manter mensagens diretas em espera", "— sessions that listen to a channel (inbound)": "— conversas que escutam um canal (entrada)", "Inbound messages and background-turn failures nothing claimed — nothing vanishes silently.": "Mensagens recebidas e falhas de execução em segundo plano sem responsável — nada desaparece silenciosamente.",
  "Recurring tasks Worker runs on a schedule.": "Tarefas recorrentes que o Worker executa conforme a programação.", "+ New automation": "+ Nova automação", "Runs only while openworker-server is up — a missed schedule catches up once when it next starts.": "Executa somente enquanto o openworker-server estiver ativo — uma programação perdida é recuperada uma vez na próxima inicialização.", "Start from a template": "Comece com um modelo", "START FROM A TEMPLATE": "COMECE COM UM MODELO", "No scheduled tasks yet — use a template above, click": "Ainda não há tarefas agendadas — use um modelo acima, clique em", ", or just ask Worker in a session.": ", ou peça diretamente ao Worker em uma conversa.", "or just ask Worker in a session.": "ou peça diretamente ao Worker em uma conversa.", "New automation": "Nova automação", "Title (e.g. Daily standup notes)": "Título (ex.: anotações diárias da reunião)", "What should it do each run? (e.g. Summarize today's calendar and open tasks.)": "O que ela deve fazer em cada execução? (ex.: resumir a agenda de hoje e as tarefas abertas.)", "At": "Às", "Repeat": "Repetir", "Weekends": "Fins de semana", "Paused": "Pausada", "Instructions": "Instruções", "Allowed without asking": "Permitido sem perguntar", "Runs": "Execuções", "Run now": "Executar agora", "Delete automation": "Excluir automação", "Saving…": "Salvando…", "Revoke": "Revogar", "No runs yet.": "Ainda não há execuções.",
  "Pipeline digest": "Resumo do pipeline", "Deals that moved — and deals going quiet — posted to Slack.": "Negócios que avançaram — e os que estão parados — publicados no Slack.", "Morning brief": "Resumo matinal", "Calendar and unread email, summarized before your day starts.": "Agenda e e-mails não lidos resumidos antes de o seu dia começar.", "Morning news briefing": "Resumo de notícias matinal", "A 5-bullet tech & world news digest, saved as markdown.": "Um resumo de tecnologia e notícias do mundo em 5 tópicos, salvo em Markdown.", "Inbox digest": "Resumo da caixa de entrada", "One short digest of your unread email.": "Um resumo curto dos seus e-mails não lidos.", "Folder cleanup": "Organização de pastas", "Sort recent Downloads into tidy folders by type.": "Organize os Downloads recentes em pastas por tipo.", "Weekly": "Semanal", "Daily": "Diariamente", "Weekdays": "Dias úteis", "No connections needed · Daily": "Nenhuma conexão necessária · Diariamente", "No connections needed · Weekly": "Nenhuma conexão necessária · Semanal",
  "Mondays": "Segundas-feiras", "Tuesdays": "Terças-feiras", "Wednesdays": "Quartas-feiras", "Thursdays": "Quintas-feiras", "Fridays": "Sextas-feiras", "Saturdays": "Sábados", "Sundays": "Domingos", "Every day": "Todos os dias", "Set up": "Configurar", "Connections, delivery & schedule": "Conexões, entrega e agendamento", "Delivery & schedule": "Entrega e agendamento", "Connected": "Conectado", "Connect": "Conectar", "Cancel": "Cancelar", "Creating…": "Criando…", "Create automation": "Criar automação", "Repository": "Repositório", "Post to channel": "Publicar no canal", "When": "Quando", "Deliver to": "Entregar em", "In the app": "No aplicativo", "Time": "Horário", "Day": "Dia",
  "What should we produce?": "O que devemos produzir?", "Pick a task to start — I'll do the work and save the result. Or just type what you need below.": "Escolha uma tarefa para começar — farei o trabalho e salvarei o resultado. Ou escreva abaixo o que você precisa.", "Analyze the files in a directory": "Analisar os arquivos em uma pasta", "I'll read them and summarize what matters": "Vou lê-los e resumir o que importa", "Create a report from my HubSpot leads": "Criar um relatório dos meus leads do HubSpot", "Sources, stages, and who needs follow-up": "Fontes, estágios e quem precisa de acompanhamento", "Automate a weekly GitHub progress report to Slack": "Automatizar um relatório semanal de progresso do GitHub no Slack", "Repo activity, summarized and posted every Friday": "Atividade do repositório, resumida e publicada toda sexta-feira", "Configure ›": "Configurar ›", "Start →": "Iniciar →",
  "Chat and explore — no edits or commands": "Converse e explore — sem edições ou comandos", "Ask before edits and commands": "Pergunte antes de edições e comandos", "Bypass approvals": "Ignorar aprovações", "Run everything without asking — approvals off": "Execute tudo sem perguntar — aprovações desativadas", "Approvals & questions go to the Inbox; the agent keeps working.": "Aprovações e perguntas vão para a caixa de entrada; o agente continua trabalhando.",
  "Connectors": "Conectores", "Apps and tools your coworkers can use. Connected ones come first.": "Aplicativos e ferramentas que seus colegas podem usar. Os conectados aparecem primeiro.", "+ Add custom MCP server": "+ Adicionar servidor MCP personalizado", "Connected · 1": "Conectados · 1", "Custom · MCP": "Personalizado · MCP", "Available": "Disponíveis", "Built in": "Integrado", "Add custom MCP server": "Adicionar servidor MCP personalizado", "Remote URL": "URL remota", "Connect a hosted MCP server. If it needs sign-in, the row will offer it after the first test.": "Conecte um servidor MCP hospedado. Se precisar de login, a linha oferecerá isso após o primeiro teste.", "Name (shown in the connectors list)": "Nome (exibido na lista de conectores)", "Add & test": "Adicionar e testar", "cancel": "cancelar",
  "Providers and the models offered in the composer's picker. Keys are stored only on this computer.": "Provedores e modelos oferecidos no seletor do compositor. As chaves são armazenadas apenas neste computador.", "Not set up": "Não configurado", "Sign in with your plan": "Entre com seu plano", "No key needed": "Não precisa de chave", "IN THE COMPOSER'S PICKER": "NO SELETOR DO COMPOSITOR", "The models offered when starting a session; the black badge marks the default. Add more from a provider's card above.": "Os modelos oferecidos ao iniciar uma conversa; o selo preto marca o padrão. Adicione mais pelo cartão do provedor acima.", "default": "padrão",
  "How sessions spend tokens — attachment handling and long-history compaction.": "Como as conversas gastam tokens — tratamento de anexos e compactação de histórico longo.", "Token savings": "Economia de tokens", "PDF attachments travel with every turn of a conversation, so large documents multiply what you spend on tokens.": "Anexos PDF acompanham cada turno da conversa, portanto documentos grandes multiplicam o gasto de tokens.", "PDFs on models without native PDF support": "PDFs em modelos sem suporte nativo a PDF", "Extract text": "Extrair texto", "Send page images": "Enviar imagens das páginas", "Max pages": "Máximo de páginas", "Max size": "Tamanho máximo", "Context compaction": "Compactação de contexto", "Compact at": "Compactar em", "Summarizer model": "Modelo de resumo", "Session’s own model (default)": "Modelo da própria conversa (padrão)",
  "Reusable instructions the worker can follow in every conversation. Off here means off everywhere.": "Instruções reutilizáveis que o agente pode seguir em todas as conversas. Desativado aqui significa desativado em todo lugar.", "+ Add skill": "+ Adicionar habilidade", "No skills yet — Add skill teaches your worker its first one, like “prepare my Monday status report”.": "Ainda não há habilidades — Adicionar habilidade ensina a primeira ao seu agente, como “preparar meu relatório de status de segunda-feira”.",
  "Speak naturally in the composer. Recordings and transcripts stay on this device.": "Fale naturalmente no compositor. Gravações e transcrições ficam neste dispositivo.", "Voice Input setup is available in the OpenWorker desktop app.": "A configuração de entrada de voz está disponível no aplicativo desktop OpenWorker.", "Your coworkers can remember useful things about you between conversations. Everything they know is listed here.": "Seus colegas podem lembrar informações úteis sobre você entre conversas. Tudo o que sabem está listado aqui.", "Remember new things about me": "Lembrar novas coisas sobre mim", "What I've learned about you": "O que aprendi sobre você", "Your instructions": "Suas instruções"
  ,"Manage your coworkers and add new ones.": "Gerencie seus colegas de trabalho e adicione novos.", "Coworkers are agents specialized for a particular role or task. They come equipped with the tools and skills to be successful in that role. Enabling a coworker lets you pick it when starting a conversation.": "Colegas de trabalho são agentes especializados em uma função ou tarefa. Eles vêm com as ferramentas e habilidades necessárias para ter sucesso nessa função. Ao ativar um colega, você poderá escolhê-lo ao iniciar uma conversa.", "General": "Geral", "Security": "Segurança", "Default": "Padrão", "Produce a deliverable — research, analysis, scripts": "Produz uma entrega — pesquisa, análise, scripts", "Work in a codebase — files, git, shell": "Trabalha em uma base de código — arquivos, git, shell", "Review Terraform & cloud config — read-only, evidence first": "Revisa Terraform e configuração de nuvem — somente leitura, evidências primeiro", "Vulnerable dependencies — audit, minimal upgrades, PRs": "Dependências vulneráveis — auditoria, atualizações mínimas, PRs", "Find and fix security issues — scan, triage, PR": "Encontra e corrige problemas de segurança — varredura, triagem, PR", "Install a coworker": "Instalar um colega de trabalho", "A small meter showing how full the model’s context window is. Turn it off to show this session’s token total instead; either way the full breakdown is one click away.": "Um pequeno medidor mostra quanto da janela de contexto do modelo está ocupada. Desative-o para mostrar o total de tokens desta conversa; de qualquer forma, o detalhamento completo está a um clique.", "Adds an Auto-approve option to the mode picker. In that mode, your session model reviews each action that would normally need approval and clears the routine ones; anything doubtful still asks you. It can never allow something the rules block. One extra model call per check, billed to your usage.": "Adiciona uma opção de aprovação automática ao seletor de modo. Nesse modo, o modelo da conversa revisa cada ação que normalmente exigiria aprovação e libera as rotineiras; qualquer dúvida ainda pede sua confirmação. Ele nunca permite algo bloqueado pelas regras. Há uma chamada extra ao modelo por verificação, cobrada do seu uso.", "Shadow evaluation (for measuring)": "Avaliação em sombra (para medição)", "On any mode, the reviewer records what it would have decided next to your own choice — without changing anything. Lets you see how it would behave before trusting it. Also costs one model call per approval.": "Em qualquer modo, o revisor registra o que teria decidido ao lado da sua escolha — sem alterar nada. Isso permite ver como ele se comportaria antes de confiar nele. Também consome uma chamada ao modelo por aprovação.", "Each conversation gets its own folder under this location. Existing conversations keep their current folder; you can grant access to more folders inside any conversation.": "Cada conversa recebe sua própria pasta neste local. Conversas existentes mantêm a pasta atual; você pode conceder acesso a mais pastas dentro de qualquer conversa.", "Trusted projects may manage their command allowances in .coworker/config.toml.": "Projetos confiáveis podem gerenciar suas permissões de comando em .coworker/config.toml.", "Activity": "Atividade", "Recent connector and browser tool activity. Arguments are sanitized before storage.": "Atividade recente de conectores e ferramentas do navegador. Os argumentos são sanitizados antes do armazenamento.", "session id": "id da conversa", "connector": "conector", "tool": "ferramenta", "Filter": "Filtrar", "Pending": "Pendentes", "Configure": "Configurar", "Approvals": "Aprovações", "Questions": "Perguntas", "Nothing pending.": "Nada pendente.", "Approvals, questions, and notifications from your coworkers — including sessions running unattended.": "Aprovações, perguntas e notificações dos seus colegas — incluindo conversas em execução autônoma.", "Delivered here only. Connect Slack (Connectors page) to also get these in a channel — more platforms later.": "Entregue somente aqui. Conecte o Slack (página Conectores) para recebê-las também em um canal — mais plataformas em breve.", "Unattended approvals": "Aprovações autônomas", "Direct messages": "Mensagens diretas", "Channel subscriptions": "Assinaturas de canais", "No channel subscriptions yet — add one below or ask a coworker to watch a channel.": "Ainda não há assinaturas de canais — adicione uma abaixo ou peça a um colega para acompanhar um canal.", "Choose a session…": "Escolha uma conversa…", "+ Subscribe": "+ Assinar", "Unrouted": "Sem roteamento", "Nothing here — no dropped messages or failed turns.": "Nada aqui — nenhuma mensagem descartada ou turno com falha."
  ,"Working now": "Trabalhando agora", "Sleeping (will wake itself)": "Em espera (será reativado automaticamente)", "From Slack": "Do Slack", "Session actions": "Ações da conversa", "Click again to permanently delete": "Clique novamente para excluir permanentemente", "Delete?": "Excluir?", "Group & filter conversations": "Agrupar e filtrar conversas", "New project": "Novo projeto", "Dock sidebar": "Fixar barra lateral", "Collapse sidebar": "Recolher barra lateral", "Account: not signed in": "Conta: não conectada", "Signed in to OpenWorker Cloud": "Conectado ao OpenWorker Cloud", "Sign out": "Sair", "Automations": "Automações", "Archived": "Arquivadas", "Recent": "Recentes", "PROJECTS": "PROJETOS", "No projects yet — start one with the + above.": "Ainda não há projetos — inicie um usando o + acima.", "Search chats": "Pesquisar conversas", "RECENT CHATS": "CONVERSAS RECENTES", "THIS MESSAGE": "NESTA MENSAGEM", "Photo or image": "Foto ou imagem", "Other files": "Outros arquivos", "THIS SESSION": "NESTA CONVERSA", "Project memory": "Memória do projeto", "Board": "Quadro", "PROJECT MEMORY": "MEMÓRIA DO PROJETO", "this folder": "esta pasta", "Name current memory…": "Nomear memória atual…", "Name current memory...": "Nomear memória atual...", "View & edit…": "Ver e editar…", "View & edit...": "Ver e editar...", "none": "nenhum", "Name current board…": "Nomear quadro atual…", "Name current board...": "Nomear quadro atual...", "Ask for approval": "Pedir aprovação"
});

Object.assign(legacyPhrases["es-ES"], {
  "Inbox cleanup": "Organización de la bandeja de entrada", "Review old email first, then organize it with your approval.": "Primero revisa los correos antiguos y luego organízalos con tu aprobación.", "The inbox to review and organize": "La bandeja de entrada que se revisará y organizará",
  "No skills yet —": "Aún no hay habilidades —", "teaches your worker its first one, like “prepare my Monday status report”.": "le enseña a tu agente su primera habilidad, como “preparar mi informe de estado del lunes”.",
  "Add skill": "Agregar habilidad", "Write it myself": "Escribirla yo mismo", "A name, a description, and the instructions": "Un nombre, una descripción y las instrucciones", "Import a file": "Importar un archivo", "A .zip or SKILL.md someone shared — you review before it installs": "Un archivo .zip o SKILL.md compartido; lo revisas antes de instalarlo", "Create with OpenWorker": "Crear con Worker", "Starts a conversation — the worker builds it and asks before adding it to your skills": "Inicia una conversación: Worker crea la habilidad y pregunta antes de añadirla a tus habilidades", "Upload a skill archive": "Subir un archivo de habilidad",
  "Channel where an Unattended session posts Approve/Deny buttons. Currently mirroring to": "Canal donde una conversación autónoma publica botones de Aprobar/Rechazar. Actualmente se refleja en", "in-app Inbox only": "solo la bandeja de entrada de la aplicación", "Set": "Establecer", "clear": "limpiar", "Direct messages": "Mensajes directos", "Session that handles DMs to the bot. With none, DMs park under Unrouted below.": "Conversación que gestiona los mensajes directos al bot. Sin una definida, los mensajes quedan en Sin enrutar abajo.", "No session — park DMs": "Ninguna conversación: dejar mensajes directos en espera", "Channel subscriptions": "Suscripciones a canales", "— sessions that listen to a channel (inbound)": "— conversaciones que escuchan un canal (entrada)", "Inbound messages and background-turn failures nothing claimed — nothing vanishes silently.": "Mensajes entrantes y fallos de ejecución en segundo plano sin gestionar; nada desaparece silenciosamente.", "Unrouted": "Sin enrutar",
  "Automations": "Automatizaciones", "Recurring tasks Worker runs on a schedule.": "Tareas recurrentes que Worker ejecuta según un horario.", "+ New automation": "+ Nueva automatización", "Runs only while openworker-server is up — a missed schedule catches up once when it next starts.": "Solo se ejecuta mientras openworker-server está activo; una programación perdida se recupera una vez al iniciarse de nuevo.", "Start from a template": "Comienza con una plantilla", "START FROM A TEMPLATE": "COMIENZA CON UNA PLANTILLA", "No scheduled tasks yet — use a template above, click": "Aún no hay tareas programadas; usa una plantilla de arriba, haz clic en", ", or just ask Worker in a session.": ", o pídeselo directamente a Worker en una conversación.", "or just ask Worker in a session.": "o pídeselo directamente a Worker en una conversación.", "New automation": "Nueva automatización", "Title (e.g. Daily standup notes)": "Título (p. ej., notas diarias de la reunión)", "What should it do each run? (e.g. Summarize today's calendar and open tasks.)": "¿Qué debe hacer en cada ejecución? (p. ej., resumir el calendario de hoy y las tareas abiertas.)", "At": "A las", "Repeat": "Repetir", "Weekends": "Fines de semana", "Paused": "Pausada", "Instructions": "Instrucciones", "Allowed without asking": "Permitido sin preguntar", "Runs": "Ejecuciones", "Run now": "Ejecutar ahora", "Delete automation": "Eliminar automatización", "Saving…": "Guardando…", "Revoke": "Revocar", "No runs yet.": "Aún no hay ejecuciones.",
  "Pipeline digest": "Resumen del pipeline", "Deals that moved — and deals going quiet — posted to Slack.": "Negocios que avanzaron y negocios que se están deteniendo, publicados en Slack.", "Morning brief": "Resumen matutino", "Calendar and unread email, summarized before your day starts.": "Calendario y correos sin leer resumidos antes de que empiece tu día.", "Morning news briefing": "Resumen de noticias matutino", "A 5-bullet tech & world news digest, saved as markdown.": "Un resumen de tecnología y noticias del mundo en 5 puntos, guardado como Markdown.", "Inbox digest": "Resumen de la bandeja de entrada", "One short digest of your unread email.": "Un resumen breve de tus correos sin leer.", "Folder cleanup": "Organización de carpetas", "Sort recent Downloads into tidy folders by type.": "Organiza las Descargas recientes en carpetas ordenadas por tipo.", "Weekly": "Semanal", "Daily": "Diariamente", "Weekdays": "Días laborables", "No connections needed · Daily": "No se necesitan conexiones · Diariamente", "No connections needed · Weekly": "No se necesitan conexiones · Semanal",
  "Mondays": "Lunes", "Tuesdays": "Martes", "Wednesdays": "Miércoles", "Thursdays": "Jueves", "Fridays": "Viernes", "Saturdays": "Sábados", "Sundays": "Domingos", "Every day": "Todos los días", "Set up": "Configurar", "Connections, delivery & schedule": "Conexiones, entrega y programación", "Delivery & schedule": "Entrega y programación", "Connected": "Conectado", "Connect": "Conectar", "Cancel": "Cancelar", "Creating…": "Creando…", "Create automation": "Crear automatización", "Repository": "Repositorio", "Post to channel": "Publicar en el canal", "When": "Cuándo", "Deliver to": "Entregar en", "In the app": "En la aplicación", "Time": "Hora", "Day": "Día",
  "What should we produce?": "¿Qué debemos producir?", "Pick a task to start — I'll do the work and save the result. Or just type what you need below.": "Elige una tarea para empezar: haré el trabajo y guardaré el resultado. O escribe abajo lo que necesitas.", "Analyze the files in a directory": "Analizar los archivos de una carpeta", "I'll read them and summarize what matters": "Los leeré y resumiré lo importante", "Create a report from my HubSpot leads": "Crear un informe de mis leads de HubSpot", "Automate a weekly GitHub progress report to Slack": "Automatizar un informe semanal de progreso de GitHub en Slack", "Configure ›": "Configurar ›", "Start →": "Iniciar →", "Chat and explore — no edits or commands": "Chatea y explora, sin ediciones ni comandos", "Ask before edits and commands": "Pregunta antes de editar y ejecutar comandos", "Bypass approvals": "Omitir aprobaciones", "Connectors": "Conectores", "Apps and tools your coworkers can use. Connected ones come first.": "Aplicaciones y herramientas que pueden usar tus compañeros. Las conectadas aparecen primero.", "+ Add custom MCP server": "+ Agregar servidor MCP personalizado", "Available": "Disponibles", "Built in": "Integrado", "Add custom MCP server": "Agregar servidor MCP personalizado", "Remote URL": "URL remota", "Add & test": "Agregar y probar", "cancel": "cancelar", "Not set up": "Sin configurar", "No key needed": "No necesita clave", "Token savings": "Ahorro de tokens", "Context compaction": "Compactación de contexto", "Extract text": "Extraer texto", "Send page images": "Enviar imágenes de páginas", "+ Add skill": "+ Agregar habilidad", "Remember new things about me": "Recordar cosas nuevas sobre mí", "Your instructions": "Tus instrucciones"
  ,"Manage your coworkers and add new ones.": "Administra tus compañeros y agrega nuevos.", "General": "General", "Security": "Seguridad", "Default": "Predeterminado", "Install a coworker": "Instalar un compañero"
});

Object.assign(legacyPhrases["pt-BR"], {
  "Skill lab": "Laboratório de habilidades", "Drafts stay separate until you approve installation in the conversation.": "Os rascunhos permanecem separados até você aprovar a instalação na conversa.", "Refresh": "Atualizar", "Description": "Descrição", "Tests": "Testes", "isolated case": "caso isolado", "isolated cases": "casos isolados", "Latest comparison": "Comparação mais recente", "No test run yet. The baseline never receives the candidate skill.": "Ainda não há execução. A referência nunca recebe a habilidade candidata.", "Run tests": "Executar testes", "Benchmark ×3": "Benchmark ×3", "Accept version": "Aceitar versão", "Discard draft": "Descartar rascunho", "Feedback": "Feedback", "What should be improved before installation?": "O que deve ser melhorado antes da instalação?", "Apply feedback": "Aplicar feedback", "Comparison details": "Detalhes da comparação", "No text output": "Sem saída de texto", "Could not start the evaluation.": "Não foi possível iniciar a avaliação.", "Could not save feedback.": "Não foi possível salvar o feedback.", "Could not discard draft.": "Não foi possível descartar o rascunho.", "protected Worker skill": "habilidade protegida do Worker", "Rate limit reached.": "O limite de requisições foi atingido. Aguarde um instante e tente novamente."
});

Object.assign(legacyPhrases["es-ES"], {
  "Reusable instructions the worker can follow in every conversation. Off here means off everywhere.": "Instrucciones reutilizables que el agente puede seguir en cada conversación. Desactivarla aquí la desactiva en todas partes.", "On": "Activada",
  "Skill lab": "Laboratorio de habilidades", "Drafts stay separate until you approve installation in the conversation.": "Los borradores se mantienen separados hasta que apruebes la instalación en la conversación.", "Refresh": "Actualizar", "Description": "Descripción", "Tests": "Pruebas", "isolated case": "caso aislado", "isolated cases": "casos aislados", "Latest comparison": "Comparación más reciente", "No test run yet. The baseline never receives the candidate skill.": "Aún no hay ejecución. La referencia nunca recibe la habilidad candidata.", "Run tests": "Ejecutar pruebas", "Benchmark ×3": "Benchmark ×3", "Accept version": "Aceptar versión", "Discard draft": "Descartar borrador", "Feedback": "Comentarios", "What should be improved before installation?": "¿Qué debe mejorarse antes de instalar?", "Apply feedback": "Aplicar comentarios", "Comparison details": "Detalles de la comparación", "No text output": "Sin salida de texto", "Could not start the evaluation.": "No se pudo iniciar la evaluación.", "Could not save feedback.": "No se pudieron guardar los comentarios.", "Could not discard draft.": "No se pudo descartar el borrador.", "protected Worker skill": "habilidad protegida de Worker", "Rate limit reached.": "Se alcanzó el límite de solicitudes. Espera un momento y vuelve a intentarlo."
});

// Retain the original English literal separately from the DOM. React owns the rendered tree,
// and this lets a user switch directly between any two languages without a page reload.
const legacyTextSources = new WeakMap<Text, string>();
const legacyAttributeSources = new WeakMap<HTMLElement, Map<string, string>>();

export function detectLanguage(languages: readonly string[] = navigator.languages): Language {
  const candidate = languages.find(Boolean)?.toLowerCase() || "";
  if (candidate.startsWith("pt")) return "pt-BR";
  if (candidate.startsWith("es")) return "es-ES";
  return FALLBACK_LANGUAGE;
}

export function getLanguage(): Language {
  try {
    const saved = localStorage.getItem(STORAGE_KEY);
    if (saved === "pt-BR" || saved === "en-US" || saved === "es-ES") return saved;
  } catch {
    // Private browsing can deny storage; detection still makes the UI usable.
  }
  return detectLanguage();
}

function applyDocumentLanguage(language: Language) {
  document.documentElement.lang = language;
}

export function initI18n() {
  const language = getLanguage();
  applyDocumentLanguage(language);
  if (!i18n.isInitialized) {
    void i18n.use(initReactI18next).init({
      resources: translationResources,
      lng: language,
      fallbackLng: FALLBACK_LANGUAGE,
      interpolation: { escapeValue: false },
      initImmediate: false,
    });
  }
}

export function changeLanguage(language: Language) {
  try {
    localStorage.setItem(STORAGE_KEY, language);
  } catch {
    // The choice remains active for the current session.
  }
  applyDocumentLanguage(language);
  void i18n.changeLanguage(language);
  window.dispatchEvent(new CustomEvent("openwork:language-pref"));
}

export function useLanguage(): [Language, (language: Language) => void] {
  const [language, setLanguage] = useState<Language>(getLanguage);
  useEffect(() => {
    const sync = () => setLanguage(getLanguage());
    window.addEventListener("openwork:language-pref", sync);
    return () => window.removeEventListener("openwork:language-pref", sync);
  }, []);
  const selectLanguage = (next: Language) => {
    setLanguage(next);
    changeLanguage(next);
  };
  return [language, selectLanguage];
}

export function formatDate(value: Date | number | string, options?: Intl.DateTimeFormatOptions) {
  return new Intl.DateTimeFormat(i18n.language || FALLBACK_LANGUAGE, options).format(new Date(value));
}

export function formatNumber(value: number, options?: Intl.NumberFormatOptions) {
  return new Intl.NumberFormat(i18n.language || FALLBACK_LANGUAGE, options).format(value);
}

function replaceText(value: string, language: Language) {
  const translated = language === "en-US" ? undefined : legacyPhrases[language][value.trim()];
  if (!translated) return value;
  const leading = value.match(/^\s*/)?.[0] || "";
  const trailing = value.match(/\s*$/)?.[0] || "";
  return `${leading}${translated}${trailing}`;
}

function isLegacyPhrase(value: string) {
  return Object.prototype.hasOwnProperty.call(legacyPhrases["pt-BR"], value.trim());
}

function isLocalizedSource(source: string, value: string) {
  return supportedLanguages.some((language) => value === replaceText(source, language));
}

function localizeLegacyDom(root: HTMLElement, language: Language) {
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
  const nodes: Text[] = [];
  let node: Node | null;
  while ((node = walker.nextNode())) nodes.push(node as Text);
  for (const text of nodes) {
    const parent = text.parentElement;
    if (!parent || parent.closest("pre, code, [data-i18n-skip], .markdown-body, .transcript")) continue;
    const knownSource = legacyTextSources.get(text);
    if (!knownSource && !isLegacyPhrase(text.data)) continue;
    // If React changed a dynamic label in place, the current text is a new source.
    // Reusing the prior source would translate a stale state (e.g. Start after Stop).
    const source = knownSource && isLocalizedSource(knownSource, text.data)
      ? knownSource
      : text.data;
    legacyTextSources.set(text, source);
    const next = replaceText(source, language);
    if (next !== text.data) text.data = next;
  }
  root.querySelectorAll<HTMLElement>("[aria-label], [title], [placeholder]").forEach((element) => {
    if (element.closest("[data-i18n-skip], .transcript")) return;
    for (const attribute of ["aria-label", "title", "placeholder"]) {
      const current = element.getAttribute(attribute);
      if (!current) continue;
      const existing = legacyAttributeSources.get(element)?.get(attribute);
      if (!existing && !isLegacyPhrase(current)) continue;
      const sources = legacyAttributeSources.get(element) ?? new Map<string, string>();
      legacyAttributeSources.set(element, sources);
      const source = existing && isLocalizedSource(existing, current)
        ? existing
        : current;
      sources.set(attribute, source);
      element.setAttribute(attribute, replaceText(source, language));
    }
  });
}

/** Keeps legacy static UI labels in sync while feature surfaces adopt semantic i18n keys. */
export function LegacyUiLocalizer() {
  const { i18n: instance } = useTranslation();
  useEffect(() => {
    const root = document.getElementById("root");
    if (!root) return;
    const language = getLanguage();
    const translate = () => localizeLegacyDom(root, language);
    translate();
    const observer = new MutationObserver(translate);
    observer.observe(root, { childList: true, subtree: true, characterData: true });
    return () => observer.disconnect();
  }, [instance.language]);
  return null;
}
