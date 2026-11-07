import React, { useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import { action, subscribe } from "./bridge";
import "./style.css";
type Pending = {
  id: string;
  session_id: string;
  kind: string;
  tool?: string;
  question?: string;
};
function App() {
  const [prompt, setPrompt] = useState("");
  const [html, setHtml] = useState("");
  const [settings, setSettings] = useState(false);
  const [provider, setProvider] = useState("OpenAI");
  const [api, setApi] = useState("responses");
  const [url, setUrl] = useState("https://api.openai.com/v1");
  const [model, setModel] = useState("");
  const [key, setKey] = useState("");
  const [status, setStatus] = useState("Ready when you are");
  const [busy, setBusy] = useState(false);
  const [messages, setMessages] = useState<string[]>([]);
  const [pending, setPending] = useState<Pending[]>([]);
  const [answer, setAnswer] = useState("");
  const handleError = (e: unknown) => {
    setStatus(e instanceof Error ? e.message : String(e));
    setBusy(false);
  };
  useEffect(() => {
    let disposed = false;
    let stop = () => {};
    subscribe((event) => {
      if (event.type === "project") {
        setMessages(event.messages as string[]);
        setHtml(String(event.url ?? ""));
        const saved = event.settings as {
          provider: string;
          api: string;
          baseUrl: string;
          model: string;
        } | null;
        if (saved) {
          setProvider(saved.provider);
          setApi(saved.api);
          setUrl(saved.baseUrl);
          setModel(saved.model);
        }
      }
      if (event.type === "preview") {
        setHtml(String(event.url));
        setStatus("Your game is ready");
      }
      if (event.type === "activity") setStatus(String(event.message));
      if (event.type === "result") {
        const r = event.result as {
          status: string;
          finalOutput?: string;
          interruptions?: Pending[];
        };
        setBusy(false);
        setPending(r.interruptions ?? []);
        setStatus(
          r.status === "completed"
            ? "Ready for your next idea"
            : r.status === "interrupted"
              ? "Your input is needed"
              : r.status,
        );
      }
      if (event.type === "error") handleError(event.message);
    })
      .then((off) => {
        if (disposed) off();
        else {
          stop = off;
          // Reattach after the event listener exists, including parked approvals.
          void action({ type: "restore" }).catch(handleError);
        }
      })
      .catch(handleError);
    return () => {
      disposed = true;
      stop();
    };
  }, []);
  const send = async () => {
    if (!prompt.trim() || busy) return;
    setBusy(true);
    setStatus("Building your idea…");
    setMessages((m) => [...m, prompt]);
    const text = prompt;
    setPrompt("");
    try {
      await action({ type: "send", text, model: { provider, name: model } });
    } catch (e) {
      handleError(e);
    }
  };
  const decide = async (i: Pending, decision?: string) => {
    setPending([]);
    setBusy(true);
    try {
      await action({ type: "respond", interaction: i, decision, answer });
      setAnswer("");
    } catch (e) {
      handleError(e);
    }
  };
  return (
    <div className="app">
      <header>
        <div className="brand">
          <span className="mark">✳</span>Arcade
          <span className="divider" />{" "}
          <span className="muted">Made for your imagination</span>
        </div>
        <button onClick={() => setSettings(true)}>Model settings</button>
      </header>
      <main>
        <aside>
          <div className="intro">
            <span className="eyebrow">YOUR NEXT LITTLE OBSESSION</span>
            <h1>
              What will
              <br />
              you create?
            </h1>
            <p>Dream up a game. Make it yours.</p>
          </div>
          <div className="conversation">
            {messages.length === 0 ? (
              <div className="ideas">
                {[
                  "A neon Snake game",
                  "A cozy word puzzle",
                  "A tiny space adventure",
                ].map((text) => (
                  <button
                    key={text}
                    onClick={() => setPrompt("Build " + text.toLowerCase())}
                  >
                    {text}
                    <span>↗</span>
                  </button>
                ))}
              </div>
            ) : (
              messages.map((m, i) => (
                <p className="message" key={i}>
                  {m}
                </p>
              ))
            )}
            {pending.map((i) => (
              <div className="permission" key={i.id}>
                <strong>
                  {i.kind === "approval" ? `Allow ${i.tool}?` : i.question}
                </strong>
                {i.kind === "approval" ? (
                  <div className="choices">
                    <button onClick={() => decide(i, "once")}>
                      Allow once
                    </button>
                    <button onClick={() => decide(i, "session")}>
                      This session
                    </button>
                    <button onClick={() => decide(i, "deny")}>Deny</button>
                  </div>
                ) : (
                  <>
                    <input
                      value={answer}
                      onChange={(e) => setAnswer(e.target.value)}
                      placeholder="Your answer"
                    />
                    <button onClick={() => decide(i)}>Send answer</button>
                  </>
                )}
              </div>
            ))}
          </div>
          <div className="composer">
            <textarea
              aria-label="Describe your game"
              value={prompt}
              onChange={(e) => setPrompt(e.target.value)}
              placeholder="A game where…"
            />
            <div>
              <span className="status" role="status">
                {status}
              </span>
              {busy ? (
                <button
                  onClick={() => action({ type: "cancel" }).catch(handleError)}
                >
                  Stop
                </button>
              ) : (
                <button
                  className="primary"
                  disabled={!prompt.trim()}
                  onClick={send}
                >
                  Create ↗
                </button>
              )}
            </div>
          </div>
          <small>
            Preview tools run with this app’s permissions. Your approval is
            required.
          </small>
        </aside>
        <section className="preview">
          <div className="previewbar">
            <span>Playground</span>
            <button
              disabled={busy}
              onClick={() => {
                action({ type: "new" })
                  .then(() => {
                    setHtml("");
                    setMessages([]);
                    setPending([]);
                  })
                  .catch(handleError);
              }}
            >
              New game
            </button>
          </div>
          {html ? (
            <iframe
              title="Game preview"
              sandbox="allow-scripts"
              referrerPolicy="no-referrer"
              src={html}
            />
          ) : (
            <div className="empty">
              <div className="orb">✦</div>
              <h2>
                A little idea.
                <br />A whole new world.
              </h2>
              <p>Your playable game will appear here.</p>
            </div>
          )}
        </section>
      </main>
      {settings && (
        <div className="overlay">
          <form
            className="settings"
            onSubmit={async (e) => {
              e.preventDefault();
              try {
                await action({
                  type: "provider",
                  model,
                  provider: {
                    name: provider,
                    api,
                    baseUrl: url,
                    ...(key
                      ? { credentials: { apiKey: key, storage: "secure" } }
                      : {}),
                  },
                });
                setKey("");
                setSettings(false);
                setStatus("Model connected");
              } catch (e) {
                handleError(e);
              }
            }}
          >
            <h2>Choose your model</h2>
            <p>
              Your key stays in the platform’s secure store. Model changes apply
              to new games.
            </p>
            <label>
              Provider
              <input
                value={provider}
                onChange={(e) => setProvider(e.target.value)}
                required
              />
            </label>
            <label>
              API
              <select
                aria-label="API"
                value={api}
                onChange={(e) => setApi(e.target.value)}
              >
                <option value="responses">Responses</option>
                <option value="chat-completions">
                  Chat Completions · Ollama / vLLM
                </option>
                <option value="anthropic">Anthropic Messages</option>
              </select>
            </label>
            <label>
              Base URL
              <input
                value={url}
                onChange={(e) => setUrl(e.target.value)}
                required
              />
            </label>
            <label>
              Model ID
              <input
                value={model}
                onChange={(e) => setModel(e.target.value)}
                placeholder="Exact model ID"
                required
              />
            </label>
            <label>
              API key · optional for local models
              <input
                type="password"
                autoComplete="off"
                value={key}
                onChange={(e) => setKey(e.target.value)}
              />
            </label>
            <div className="choices">
              <button
                type="button"
                onClick={() => {
                  setKey("");
                  setSettings(false);
                }}
              >
                Cancel
              </button>
              <button className="primary">Save</button>
            </div>
          </form>
        </div>
      )}
    </div>
  );
}
createRoot(document.getElementById("root")!).render(<App />);
