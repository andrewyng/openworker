import {
  $createParagraphNode,
  $createTextNode,
  $getRoot,
  $getSelection,
  $getNodeByKey,
  $isElementNode,
  $isRangeSelection,
  $isTextNode,
  COMMAND_PRIORITY_HIGH,
  COMMAND_PRIORITY_LOW,
  DecoratorNode,
  KEY_ARROW_DOWN_COMMAND,
  KEY_ARROW_UP_COMMAND,
  KEY_ENTER_COMMAND,
  KEY_ESCAPE_COMMAND,
  KEY_TAB_COMMAND,
  SELECTION_CHANGE_COMMAND,
  type EditorConfig,
  type LexicalEditor,
  type LexicalNode,
  type NodeKey,
  type SerializedLexicalNode,
  type Spread,
} from "lexical";
import { LexicalComposer } from "@lexical/react/LexicalComposer";
import { ContentEditable } from "@lexical/react/LexicalContentEditable";
import { LexicalErrorBoundary } from "@lexical/react/LexicalErrorBoundary";
import { HistoryPlugin } from "@lexical/react/LexicalHistoryPlugin";
import { useLexicalComposerContext } from "@lexical/react/LexicalComposerContext";
import { OnChangePlugin } from "@lexical/react/LexicalOnChangePlugin";
import { PlainTextPlugin } from "@lexical/react/LexicalPlainTextPlugin";
import {
  forwardRef,
  useEffect,
  useImperativeHandle,
  useMemo,
  useRef,
  useState,
} from "react";
import { openSkill } from "../skillEvents";
import type { PromptPart, SkillRef } from "../types";
import { Icon } from "./Icon";

type SerializedSkillNode = Spread<
  { type: "skill"; version: 1; skill: SkillRef },
  SerializedLexicalNode
>;

class SkillNode extends DecoratorNode<JSX.Element> {
  __skill: SkillRef;

  static getType() {
    return "skill";
  }

  static clone(node: SkillNode) {
    return new SkillNode(node.__skill, node.__key);
  }

  static importJSON(serialized: SerializedSkillNode) {
    return new SkillNode(serialized.skill);
  }

  constructor(skill: SkillRef, key?: NodeKey) {
    super(key);
    this.__skill = skill;
  }

  createDOM(_config: EditorConfig) {
    const span = document.createElement("span");
    span.className = "inline";
    return span;
  }

  updateDOM() {
    return false;
  }

  isInline() {
    return true;
  }

  getTextContent() {
    return `/${this.__skill.name}`;
  }

  exportJSON(): SerializedSkillNode {
    return { ...super.exportJSON(), type: "skill", version: 1, skill: this.__skill };
  }

  decorate() {
    return (
      <button
        type="button"
        contentEditable={false}
        data-testid="composer-skill-chip"
        className="mx-0.5 inline-flex items-center gap-1 rounded-md bg-accentSoft px-1.5 py-0.5 text-[13px] font-medium text-accent hover:bg-accentSoft/70"
        title={`${this.__skill.source || "skill"} · ${this.__skill.path}`}
        onClick={() => openSkill(this.__skill)}
      >
        <Icon name="diamond" size={12} />
        {this.__skill.name}
      </button>
    );
  }
}

function $createSkillNode(skill: SkillRef) {
  return new SkillNode(skill);
}

function pushText(parts: PromptPart[], text: string) {
  if (!text) return;
  const previous = parts[parts.length - 1];
  if (previous?.type === "text") previous.text += text;
  else parts.push({ type: "text", text });
}

function draftFromEditor(): { text: string; parts: PromptPart[] } {
  const parts: PromptPart[] = [];
  const visit = (node: LexicalNode) => {
    if (node instanceof SkillNode) {
      parts.push({ type: "skill", ...node.__skill });
      return;
    }
    if ($isTextNode(node)) {
      pushText(parts, node.getTextContent());
      return;
    }
    if ($isElementNode(node)) node.getChildren().forEach(visit);
    else pushText(parts, node.getTextContent());
  };
  const blocks = $getRoot().getChildren();
  blocks.forEach((block, index) => {
    if (index) pushText(parts, "\n");
    visit(block);
  });
  return {
    parts,
    text: parts
      .map((part) => (part.type === "text" ? part.text : `/${part.name}`))
      .join(""),
  };
}

type ActiveSlash = {
  key: NodeKey;
  start: number;
  end: number;
  query: string;
};

function activeSlash(): ActiveSlash | null {
  const selection = $getSelection();
  if (!$isRangeSelection(selection) || !selection.isCollapsed()) return null;
  const node = selection.anchor.getNode();
  if (!$isTextNode(node)) return null;
  const end = selection.anchor.offset;
  const before = node.getTextContent().slice(0, end);
  const match = before.match(/(?:^|\s)\/([^\s/]*)$/);
  if (!match) return null;
  const query = match[1] || "";
  return { key: node.getKey(), start: end - query.length - 1, end, query };
}

function EditorCapture({ onReady }: { onReady: (editor: LexicalEditor) => void }) {
  const [editor] = useLexicalComposerContext();
  useEffect(() => onReady(editor), [editor, onReady]);
  return null;
}

function SkillChooser({
  skills,
  onSubmit,
}: {
  skills: SkillRef[];
  onSubmit: () => void;
}) {
  const [editor] = useLexicalComposerContext();
  const [slash, setSlash] = useState<ActiveSlash | null>(null);
  const [selected, setSelected] = useState(0);
  const slashRef = useRef<ActiveSlash | null>(null);
  const selectedRef = useRef(0);

  const matches = useMemo(() => {
    if (!slash) return [];
    const query = slash.query.toLowerCase();
    return skills
      .filter((skill) =>
        `${skill.name} ${skill.description || ""} ${skill.source || ""}`
          .toLowerCase()
          .includes(query),
      )
      .slice(0, 8);
  }, [skills, slash]);
  const matchesRef = useRef<SkillRef[]>([]);
  useEffect(() => {
    matchesRef.current = matches;
    if (selected >= matches.length) {
      selectedRef.current = 0;
      setSelected(0);
    }
  }, [matches, selected]);
  useEffect(() => {
    slashRef.current = slash;
  }, [slash]);

  const choose = (skill: SkillRef) => {
    const target = slashRef.current;
    if (!target) return;
    editor.update(() => {
      const node = $getNodeByKey(target.key);
      const selection = $getSelection();
      if (!$isTextNode(node) || !$isRangeSelection(selection)) return;
      selection.setTextNodeRange(node, target.start, node, target.end);
      selection.removeText();
      selection.insertNodes([$createSkillNode(skill), $createTextNode(" ")]);
    });
    setSlash(null);
    editor.focus();
  };

  useEffect(
    () => {
      const updateSlash = () => {
        const next = activeSlash();
        setSlash(next);
        if (!next) {
          selectedRef.current = 0;
          setSelected(0);
        }
      };
      const unregisterUpdate = editor.registerUpdateListener(({ editorState }) => {
        editorState.read(() => {
          updateSlash();
        });
      });
      const unregisterSelection = editor.registerCommand(
        SELECTION_CHANGE_COMMAND,
        () => {
          updateSlash();
          return false;
        },
        COMMAND_PRIORITY_LOW,
      );
      return () => {
        unregisterUpdate();
        unregisterSelection();
      };
    },
    [editor],
  );

  useEffect(() => {
    const move = (delta: number) => {
      const count = matchesRef.current.length;
      if (!slashRef.current || !count) return false;
      selectedRef.current = (selectedRef.current + delta + count) % count;
      setSelected(selectedRef.current);
      return true;
    };
    const selectCurrent = () => {
      const skill = matchesRef.current[selectedRef.current];
      if (!slashRef.current || !skill) return false;
      choose(skill);
      return true;
    };
    return [
      editor.registerCommand(KEY_ARROW_DOWN_COMMAND, () => move(1), COMMAND_PRIORITY_HIGH),
      editor.registerCommand(KEY_ARROW_UP_COMMAND, () => move(-1), COMMAND_PRIORITY_HIGH),
      editor.registerCommand(KEY_TAB_COMMAND, () => selectCurrent(), COMMAND_PRIORITY_HIGH),
      editor.registerCommand(
        KEY_ESCAPE_COMMAND,
        () => {
          if (!slashRef.current) return false;
          setSlash(null);
          return true;
        },
        COMMAND_PRIORITY_HIGH,
      ),
      editor.registerCommand(
        KEY_ENTER_COMMAND,
        (event) => {
          if (selectCurrent()) {
            event?.preventDefault();
            return true;
          }
          if (event?.shiftKey) return false;
          event?.preventDefault();
          onSubmit();
          return true;
        },
        COMMAND_PRIORITY_HIGH,
      ),
    ].reduce((dispose, unregister) => () => {
      dispose();
      unregister();
    });
  }, [editor, onSubmit]);

  if (!slash || !matches.length) return null;
  return (
    <div
      role="listbox"
      aria-label="Skills"
      className="absolute z-50 bottom-full left-2 right-2 mb-1 max-h-72 overflow-auto rounded-xl border border-line bg-panel p-1.5 shadow-2xl"
    >
      {matches.map((skill, index) => (
        <button
          type="button"
          role="option"
          aria-selected={index === selected}
          key={`${skill.path}:${index}`}
          className={
            "flex w-full items-start gap-2 rounded-lg px-2.5 py-2 text-left " +
            (index === selected ? "bg-paper text-ink" : "text-muted hover:bg-paper")
          }
          onMouseDown={(event) => event.preventDefault()}
          onClick={() => choose(skill)}
        >
          <Icon name="diamond" size={14} className="mt-0.5 shrink-0 text-accent" />
          <span className="min-w-0">
            <span className="block text-[13px] font-medium text-ink">{skill.name}</span>
            <span className="block truncate text-[11.5px]">{skill.description}</span>
            <span className="block truncate text-[10.5px] text-faint">
              {skill.source} · {skill.path}
            </span>
          </span>
        </button>
      ))}
    </div>
  );
}

export interface SkillPromptEditorHandle {
  clear: () => void;
  focus: () => void;
  setText: (text: string) => void;
  appendText: (text: string) => void;
}

interface Props {
  initialText?: string;
  placeholder: string;
  skills: SkillRef[];
  onChange: (draft: { text: string; parts: PromptPart[] }) => void;
  onSubmit: () => void;
  onPaste?: (event: React.ClipboardEvent<HTMLDivElement>) => void;
}

export const SkillPromptEditor = forwardRef<SkillPromptEditorHandle, Props>(
  function SkillPromptEditor({ initialText = "", placeholder, skills, onChange, onSubmit, onPaste }, ref) {
    const [editor, setEditor] = useState<LexicalEditor | null>(null);
    useImperativeHandle(
      ref,
      () => ({
        clear: () =>
          editor?.update(() => {
            const root = $getRoot();
            root.clear();
            root.append($createParagraphNode());
          }),
        focus: () => editor?.focus(),
        setText: (text) =>
          editor?.update(() => {
            const root = $getRoot();
            root.clear();
            root.append($createParagraphNode().append($createTextNode(text)));
          }),
        appendText: (text) =>
          editor?.update(() => {
            const root = $getRoot();
            if (!root.getFirstChild()) root.append($createParagraphNode());
            root.selectEnd();
            const selection = $getSelection();
            if ($isRangeSelection(selection)) selection.insertText(text);
          }),
      }),
      [editor],
    );
    const config = useMemo(
      () => ({
        namespace: "openworker-skill-composer",
        nodes: [SkillNode],
        onError: (error: Error) => {
          throw error;
        },
        editorState: () => {
          const root = $getRoot();
          root.clear();
          root.append($createParagraphNode().append($createTextNode(initialText)));
        },
        theme: {
          paragraph: "m-0",
        },
      }),
      // Remounting handles a changed reset/prefill value; this callback is initial state only.
      // eslint-disable-next-line react-hooks/exhaustive-deps
      [],
    );

    return (
      <LexicalComposer initialConfig={config}>
        <div className="relative">
          <PlainTextPlugin
            contentEditable={
              <ContentEditable
                ref={(element) => {
                  // Preserve the long-standing automation/accessibility locator while using
                  // a rich contenteditable surface (placeholder is valid as a DOM attribute
                  // even though React's generic HTML typings omit it here).
                  element?.setAttribute("placeholder", placeholder);
                }}
                aria-label="Message"
                className="min-h-10 max-h-[5.75rem] w-full overflow-y-auto px-3.5 pb-1.5 pt-3.5 text-[14.5px] leading-[22px] outline-none"
                onPaste={onPaste}
              />
            }
            placeholder={
              <div className="pointer-events-none absolute left-3.5 top-3.5 text-[14.5px] text-faint">
                {placeholder}
              </div>
            }
            ErrorBoundary={LexicalErrorBoundary}
          />
          <OnChangePlugin
            onChange={(state) => state.read(() => onChange(draftFromEditor()))}
          />
          <HistoryPlugin />
          <EditorCapture onReady={setEditor} />
          <SkillChooser skills={skills} onSubmit={onSubmit} />
        </div>
      </LexicalComposer>
    );
  },
);
