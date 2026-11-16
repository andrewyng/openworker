import { Worker } from "node:worker_threads";
// Parse untrusted PDFs in a bounded worker. No document JavaScript or rendering.
export function extractPdf(bytes) {
  if (bytes.length > 8 * 1024 * 1024)
    return Promise.reject(Error("PDF must be 8 MB or smaller."));
  if (!bytes.subarray(0, 1024).includes(Buffer.from("%PDF-")))
    return Promise.reject(Error("Choose a PDF file."));
  return new Promise((resolve, reject) => {
    const worker = new Worker(new URL("./pdf-worker.mjs", import.meta.url), {
      workerData: new Uint8Array(bytes),
      resourceLimits: { maxOldGenerationSizeMb: 192 },
    });
    const timer = setTimeout(() => {
      worker.terminate();
      reject(Error("PDF parsing timed out."));
    }, 15000);
    worker.once("message", (value) => {
      clearTimeout(timer);
      worker.terminate();
      value.error ? reject(Error(value.error)) : resolve(value);
    });
    worker.once("error", (error) => {
      clearTimeout(timer);
      reject(error);
    });
    worker.once("exit", (code) => {
      clearTimeout(timer);
      if (code !== 0) reject(Error("PDF parser stopped."));
    });
  });
}
