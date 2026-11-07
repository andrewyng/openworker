import { parentPort, workerData } from "node:worker_threads";
import { getDocument } from "pdfjs-dist/legacy/build/pdf.mjs";
let task;
try {
  task = getDocument({
    data: workerData,
    isEvalSupported: false,
    useSystemFonts: false,
    disableFontFace: true,
  });
  const pdf = await task.promise;
  if (pdf.numPages > 80) throw Error("Use a PDF with at most 80 pages.");
  const pages = [];
  let count = 0;
  for (let number = 1; number <= pdf.numPages; number++) {
    const page = await pdf.getPage(number);
    const content = await page.getTextContent();
    const text = content.items
      .map((item) => ("str" in item ? item.str : ""))
      .join(" ");
    count += text.length;
    if (count > 180000)
      throw Error(
        "This PDF has too much text for this example. Use a shorter document.",
      );
    pages.push({ page: number, text });
    page.cleanup();
  }
  if (!pages.some((p) => p.text.trim()))
    throw Error(
      "No readable text found. Scanned PDFs need OCR; this example does not include OCR.",
    );
  parentPort.postMessage({ pages, context: JSON.stringify(pages) });
} catch (error) {
  parentPort.postMessage({ error: error.message });
} finally {
  await task?.destroy();
}
