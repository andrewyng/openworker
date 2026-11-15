const $ = (id) => document.getElementById(id);
let documentId = null,
  controller;
const headers = () => {
  const token = $("token").value;
  return token ? { Authorization: `Bearer ${token}` } : {};
};
function busy(value) {
  $("upload").disabled = value;
  $("pdf").disabled = value;
  $("ask").disabled = value || !documentId;
  $("cancel").disabled = !value || !controller;
}
$("upload").onclick = async () => {
  const file = $("pdf").files[0];
  if (!file) {
    $("status").textContent = "Choose a PDF first.";
    return;
  }
  documentId = null;
  busy(true);
  $("status").textContent = "Preparing document…";
  try {
    const response = await fetch("/api/documents", {
      method: "POST",
      headers: { ...headers(), "Content-Type": "application/pdf" },
      body: file,
    });
    const result = await response.json();
    if (!response.ok) throw Error(result.error);
    documentId = result.id;
    $("document").textContent =
      result.pages == null ? `${file.name} · PDF attached` : `${file.name} · ${result.pages} page${result.pages === 1 ? "" : "s"}`;
    $("answers").replaceChildren();
    $("answers").classList.remove("empty");
    $("status").textContent = "Document ready. Ask your first question.";
  } catch (e) {
    $("status").textContent = e.message;
  } finally {
    busy(false);
  }
};
$("ask").onclick = async () => {
  const question = $("question").value.trim();
  if (!question) return;
  controller = new AbortController();
  busy(true);
  const block = document.createElement("section");
  block.className = "answer";
  const heading = document.createElement("div");
  heading.className = "question";
  heading.textContent = question;
  const answer = document.createElement("div");
  block.append(heading, answer);
  $("answers").append(block);
  $("status").textContent = "Reading your document…";
  try {
    const response = await fetch("/api/ask", {
      method: "POST",
      headers: { ...headers(), "Content-Type": "application/json" },
      body: JSON.stringify({ id: documentId, question }),
      signal: controller.signal,
    });
    if (!response.ok) throw Error((await response.json()).error);
    const reader = response.body.getReader(),
      decoder = new TextDecoder();
    let buffer = "",
      done = false;
    for (;;) {
      const chunk = await reader.read();
      buffer += decoder.decode(chunk.value ?? new Uint8Array(), {
        stream: !chunk.done,
      });
      let newline;
      while ((newline = buffer.indexOf("\n")) >= 0) {
        const line = buffer.slice(0, newline);
        buffer = buffer.slice(newline + 1);
        if (!line) continue;
        const event = JSON.parse(line);
        if (event.type === "snapshot") {
          answer.textContent = event.text;
          $("status").textContent = "Writing an answer…";
        }
        if (event.type === "delta") {
          answer.textContent += event.text;
          $("status").textContent = "Writing an answer…";
        }
        if (event.type === "done") {
          answer.textContent = event.text;
          done = true;
        }
        if (event.type === "error") throw Error(event.text);
      }
      if (chunk.done) break;
    }
    if (!done) throw Error("The connection ended before the answer finished.");
    $("status").textContent = "Answer ready. Check the cited pages.";
  } catch (e) {
    $("status").textContent = e.name === "AbortError" ? "Stopped." : e.message;
  } finally {
    controller = null;
    busy(false);
  }
};
$("cancel").onclick = () => controller?.abort();
