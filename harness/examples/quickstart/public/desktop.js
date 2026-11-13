const $ = (id) => document.getElementById(id);
let selected = false;
function busy(value) {
  $("choose").disabled = value;
  $("summarize").disabled = value || !selected;
  $("cancel").disabled = !value;
}
$("choose").onclick = async () => {
  try {
    const result = await window.codeAtlas.choose();
    if (!result) return;
    selected = !!result.files.length;
    $("directory").textContent = result.directory;
    $("files").replaceChildren(
      ...result.files.map((path) => {
        const li = document.createElement("li");
        li.textContent = path;
        return li;
      }),
    );
    $("scope").textContent =
      `${result.files.length} file${result.files.length === 1 ? "" : "s"} · ${result.bytes.toLocaleString()} bytes · ${result.omitted} entries omitted`;
    $("status").textContent = "Review the selected files, then summarize.";
    busy(false);
  } catch (e) {
    $("status").textContent = e.message;
  }
};
$("summarize").onclick = async () => {
  busy(true);
  $("answer").textContent = "";
  $("answer").classList.remove("empty");
  $("status").textContent = "Reading the source snapshot…";
  try {
    await window.codeAtlas.summarize();
  } catch (e) {
    $("status").textContent = e.message;
    busy(false);
  }
};
$("cancel").onclick = () => window.codeAtlas.cancel();
window.codeAtlas.onEvent((event) => {
  if (event.type === "snapshot") {
    $("answer").textContent = event.text;
    $("status").textContent = "Writing a guide…";
  }
  if (event.type === "delta") {
    $("answer").textContent += event.text;
    $("status").textContent = "Writing your guide…";
  }
  if (event.type === "done") {
    $("answer").textContent = event.text;
    $("status").textContent = "Summary ready.";
    busy(false);
  }
  if (event.type === "error") {
    $("status").textContent = event.text;
    busy(false);
  }
});
