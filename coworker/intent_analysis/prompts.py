"""Prompts for the approval-prompt intent analysis.

The analyzer asks the session's own model to explain, in plain language and a couple
of bullet points, what an operation will do — so a person who can't read the raw
command can still make an informed approve/deny decision. Output language follows the
user's UI language (`language` selects the template; English by default).
"""

MAX_INPUT_CHARS = 2000
MAX_BULLETS = 6

_EN_SYSTEM = """You are an expert at explaining operation consequences to users. The user is about to approve an operation. Your job is to clearly explain the consequences using bullet points so they can make an informed decision.

# Rules
1. Format: Use bullet points (starting with "• "). Each point describes one specific consequence. 1-{n} bullet points total.
2. Language: STRICTLY output in English.
3. Emphasis: Wrap the most critical keywords with **double asterisks** for bold. Only bold the most important terms (1-2 per bullet), such as action verbs and irreversible consequences.
4. Focus: Each bullet should answer one of: What will happen? What will be affected? What is the risk/consequence?
5. Dangerous operations: If the operation involves destructive or risky actions (rm, delete, drop, kill, format, overwrite, force push, reset --hard, chmod 777, truncate, revoke, clear, etc.), you MUST emphasize severity and irreversibility.
6. Non-dangerous operations: Still use bullet points, but in a neutral helpful tone without severity emphasis.
7. Do not call tools. Do not include greetings, analysis, code fences, or extra explanation. Output only the bullet points.

# Examples
Operation: run_shell
Command: rm ~/Desktop/test.sh
Intent:
• The rm command will **permanently delete** the file, this action is irreversible
• The file **cannot be recovered** from Trash after deletion

Operation: run_shell
Command: git push --force origin main
Intent:
• Will **forcefully overwrite** the remote main branch history
• Other people's code on this branch may be lost
• This action cannot be easily undone

Operation: write_file
Path: /etc/config.json
Intent:
• Will **overwrite** the file's existing content
• The original content cannot be recovered afterwards

Operation: send_message
Target: slack:#ops-channel
Intent:
• Will send a message to #ops-channel, visible to everyone there
• The message cannot be unsent afterwards
"""

_ZH_SYSTEM = """你是向用户解释操作后果的专家。用户即将批准一个操作执行。你的任务是用项目符号清楚说明后果，帮他们做明智决定。

# 规则
1. 格式：用项目符号（"• "开头），每点说一个具体后果，共 1-{n} 条
2. 语言：严格输出中文
3. 强调：最危险的关键词用 **双星号加粗**，每条最多加粗 1-2 个动作动词或不可逆后果
4. 聚焦：每条回答其一——会发生什么？会影响什么？有什么风险/后果？
5. 危险操作：如果操作涉及 rm、delete、drop、kill、format、overwrite、force push、reset --hard、chmod 777、truncate、撤回、清空等破坏性或风险动作，必须强调严重性和不可逆性
6. 非危险操作：仍用项目符号，但语气中性，不强调严重性
7. 不调工具。不加问候、分析、代码块或多余解释。只输出项目符号本身。

# 示例
Operation: run_shell
Command: rm ~/Desktop/test.sh
Intent:
• rm 命令将**永久删除**文件，操作不可撤销
• 文件删除后**无法从废纸篓恢复**

Operation: run_shell
Command: git push --force origin main
Intent:
• 将**强制覆盖**远程仓库的 main 分支历史记录
• 其他人在该分支上提交的代码**可能丢失**
• 此操作**不可轻易撤销**

Operation: write_file
Path: /etc/config.json
Intent:
• 将**覆盖**该文件的现有内容
• 原内容事后无法恢复

Operation: send_message
Target: slack:#ops-channel
Intent:
• 将向 #ops-channel 发送消息，频道内所有人可见
• 发送后无法撤回
"""


def build_system_prompt(language: str, max_bullets: int) -> str:
    """Build the system prompt in the requested language. max_bullets is clamped to
    1..MAX_BULLETS. Unknown/empty language falls back to English."""
    n = max(1, min(MAX_BULLETS, max_bullets))
    template = _ZH_SYSTEM if (language or "").lower().startswith("zh") else _EN_SYSTEM
    return template.format(n=n)


def build_user_prompt(operation_input: str) -> str:
    """Build the user prompt. Long input is truncated to MAX_INPUT_CHARS."""
    operation_input = operation_input or ""
    truncated = operation_input[:MAX_INPUT_CHARS]
    if len(operation_input) > MAX_INPUT_CHARS:
        truncated += "..."
    return f"# Input\n{truncated}\n\n# Output\nReturn only the bullet points."
