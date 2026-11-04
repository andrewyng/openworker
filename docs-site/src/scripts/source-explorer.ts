const drawer = document.querySelector<HTMLDialogElement>(
  "#example-source-drawer",
);
if (drawer) {
  const files = drawer.querySelector<HTMLElement>("[data-source-files]")!;
  const path = drawer.querySelector<HTMLElement>("[data-source-path]")!;
  const content = drawer.querySelector<HTMLElement>("[data-source-content]")!;
  const status = drawer.querySelector<HTMLElement>("[data-source-status]")!;
  const copy = drawer.querySelector<HTMLButtonElement>("[data-source-copy]")!;
  let request: AbortController | undefined;
  drawer
    .querySelector("[data-source-close]")!
    .addEventListener("click", () => drawer.close());
  drawer.addEventListener("close", () => request?.abort());
  drawer.addEventListener("click", (event) => {
    if (event.target === drawer) {
      const r = drawer.getBoundingClientRect();
      const e = event as MouseEvent;
      if (
        e.clientX < r.left ||
        e.clientX > r.right ||
        e.clientY < r.top ||
        e.clientY > r.bottom
      )
        drawer.close();
    }
  });
  copy.addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText(content.textContent || "");
      status.textContent = "Copied.";
    } catch {
      status.textContent = "Select the code to copy it.";
    }
  });
  document
    .querySelectorAll<HTMLButtonElement>("[data-example-source]")
    .forEach((button) => {
      button.addEventListener("click", async () => {
        request?.abort();
        request = new AbortController();
        files.replaceChildren();
        path.textContent = "";
        content.textContent = "";
        copy.disabled = true;
        status.textContent = "Loading source…";
        drawer.showModal();
        try {
          const response = await fetch(button.dataset.exampleSource!, {
            signal: request.signal,
          });
          if (!response.ok) throw new Error("Source unavailable");
          const data: { files: { path: string; content: string }[] } =
            await response.json();
          for (const file of data.files) {
            const item = document.createElement("button");
            item.type = "button";
            item.textContent = file.path;
            item.addEventListener("click", () => {
              files
                .querySelectorAll("button")
                .forEach((b) => b.removeAttribute("aria-current"));
              item.setAttribute("aria-current", "true");
              path.textContent = file.path;
              content.textContent = file.content;
              content.parentElement!.scrollTop = 0;
              status.textContent = "";
              copy.disabled = false;
            });
            files.append(item);
          }
          files.querySelector("button")?.click();
        } catch (error) {
          if (!(error instanceof DOMException && error.name === "AbortError"))
            status.textContent =
              "Unable to load source. The full example is linked in this guide.";
        }
      });
    });
}
