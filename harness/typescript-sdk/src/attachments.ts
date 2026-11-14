/** Ordered user inputs; file bytes are staged, never packed into a giant RPC. */
import { createHash } from "node:crypto";
import { open } from "node:fs/promises";
import { basename, extname } from "node:path";
import { HarnessError } from "./types.js";
const MAX_FILE = 8 * 1024 * 1024,
  CHUNK = 256 * 1024;
const mimes = [
  "image/png",
  "image/jpeg",
  "image/gif",
  "image/webp",
  "application/pdf",
  "text/plain",
];
const invalid = (message: string): never => {
  throw new HarnessError("InvalidInput", message);
};
export type AttachmentSource =
  | { type: "base64"; mediaType: string; data: string }
  | { type: "text"; mediaType: "text/plain"; data: string }
  | { type: "attachment"; id: string };
export type InputContent =
  | { type: "text"; text: string }
  | Attachment
  | { type: "image" | "document"; name?: string; source: AttachmentSource };
export interface UserInput {
  type?: "message";
  content: InputContent[];
}
export class Attachment {
  readonly type: "image" | "document";
  readonly name: string;
  readonly mediaType: string;
  #bytes: Buffer;
  private constructor(name: string, mediaType: string, bytes: Uint8Array) {
    if (
      !name ||
      Buffer.byteLength(name) > 240 ||
      /[\x00-\x1f\x7f/\\]/.test(name)
    )
      invalid("Invalid attachment name");
    if (!mimes.includes(mediaType))
      invalid("Unsupported attachment media type");
    if (bytes.byteLength < 1 || bytes.byteLength > MAX_FILE)
      invalid("Attachment size limit exceeded");
    if (mediaType === "text/plain") {
      try {
        new TextDecoder("utf-8", { fatal: true }).decode(bytes);
      } catch {
        invalid("Text attachment must be UTF-8");
      }
      if (bytes.includes(0)) invalid("Text attachment contains null bytes");
    }
    this.name = name;
    this.mediaType = mediaType;
    this.type = mediaType.startsWith("image/") ? "image" : "document";
    this.#bytes = Buffer.from(bytes);
    Object.freeze(this);
  }
  static fromBytes(options: {
    name: string;
    mediaType: string;
    data: Uint8Array;
  }): Attachment {
    return new Attachment(options.name, options.mediaType, options.data);
  }
  static async fromFile(
    path: string,
    options: { mediaType?: string } = {},
  ): Promise<Attachment> {
    const file = await open(path, "r");
    try {
      const stat = await file.stat();
      if (!stat.isFile() || stat.size > MAX_FILE)
        invalid("Attachment must be a bounded regular file");
      const bytes = Buffer.alloc(stat.size + 1);
      let used = 0;
      while (used < bytes.length) {
        const n = await file.read(bytes, used, bytes.length - used, null);
        if (!n.bytesRead) break;
        used += n.bytesRead;
      }
      if (used > MAX_FILE || used !== stat.size)
        invalid("Attachment changed while reading");
      const ext = extname(path).toLowerCase();
      const mime =
        options.mediaType ??
        (
          {
            ".png": "image/png",
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".gif": "image/gif",
            ".webp": "image/webp",
            ".pdf": "application/pdf",
          } as Record<string, string>
        )[ext] ??
        "text/plain";
      return new Attachment(basename(path), mime, bytes.subarray(0, used));
    } finally {
      await file.close();
    }
  }
  /** Return a copy so caller changes cannot mutate an in-flight upload. */
  bytes(): Buffer {
    return Buffer.from(this.#bytes);
  }
}
function asAttachment(
  part: Exclude<InputContent, { type: "text"; text: string }>,
): Attachment | { reference: string; type: "image" | "document" } {
  if (part instanceof Attachment) return part;
  if (
    !part ||
    !["image", "document"].includes(part.type) ||
    !part.source ||
    Object.keys(part).some((k) => !["type", "name", "source"].includes(k))
  )
    invalid("Invalid attachment content");
  if (part.source.type === "attachment") {
    if (Object.keys(part.source).some((k) => !["type", "id"].includes(k)))
      invalid("Invalid attachment source fields");
    if (!/^[A-Za-z0-9_-]{1,100}$/.test(part.source.id))
      invalid("Invalid attachment reference");
    return { reference: part.source.id, type: part.type };
  }
  let bytes: Buffer;
  const source = part.source;
  if (
    Object.keys(source).some((k) => !["type", "mediaType", "data"].includes(k))
  )
    invalid("Invalid attachment source fields");
  if (source.type === "text") {
    if (typeof source.data !== "string" || source.mediaType !== "text/plain")
      invalid("Invalid text attachment source");
    bytes = Buffer.from(source.data, "utf8");
  } else if (source.type === "base64") {
    if (
      typeof source.data !== "string" ||
      source.data.length > Math.ceil(MAX_FILE / 3) * 4 ||
      !/^(?:[A-Za-z0-9+/]{4})*(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?$/.test(
        source.data,
      )
    )
      invalid("Invalid attachment base64");
    bytes = Buffer.from(source.data, "base64");
  } else return invalid("Unsupported attachment source");
  const a = Attachment.fromBytes({
    name: part.name ?? "attachment",
    mediaType: source.mediaType,
    data: bytes,
  });
  if (a.type !== part.type) invalid("Attachment type mismatch");
  return a;
}
export function userInput(
  text: string,
  attachments: Attachment | Attachment[],
): UserInput {
  return {
    content: [
      { type: "text", text },
      ...(Array.isArray(attachments) ? attachments : [attachments]),
    ],
  };
}
export function attachmentArgument(
  value: unknown,
): value is Attachment | Attachment[] {
  return value instanceof Attachment || Array.isArray(value);
}
export async function stageInput(
  input: UserInput,
  sessionId: string,
  request: (method: string, args: Record<string, unknown>) => Promise<unknown>,
  signal?: AbortSignal,
): Promise<Record<string, unknown>[]> {
  if (
    !input ||
    (input.type !== undefined && input.type !== "message") ||
    Object.keys(input).some((k) => k !== "content" && k !== "type") ||
    !Array.isArray(input.content) ||
    input.content.length < 1 ||
    input.content.length > 32
  )
    invalid("Input content must have 1..32 blocks");
  // Snapshot all bytes before the first await; validate total size before staging.
  let size = 0,
    textSize = 0;
  const parts = input.content.map((part) => {
    if (!part || typeof part !== "object")
      invalid("Invalid input content block");
    if (part.type === "text") {
      if (Object.keys(part).some((k) => !["type", "text"].includes(k)))
        invalid("Invalid text block fields");
      if (typeof part.text !== "string" || part.text.includes("\0"))
        invalid("Invalid input text");
      textSize += Buffer.byteLength(part.text);
      return { text: part.text };
    }
    const value = asAttachment(part);
    if ("reference" in value) return value;
    const bytes = value.bytes();
    size += bytes.length;
    if (size > 16 * 1024 * 1024) invalid("User input exceeds turn limits");
    return { attachment: value, bytes };
  });
  if (size > 16 * 1024 * 1024 || textSize > 32000)
    invalid("User input exceeds turn limits");
  const result: Record<string, unknown>[] = [];
  for (const part of parts) {
    if (signal?.aborted)
      throw new HarnessError("Aborted", "Attachment upload cancelled");
    if ("text" in part) {
      result.push({ type: "text", text: part.text });
      continue;
    }
    if ("reference" in part) {
      result.push({ type: part.type, attachment_id: part.reference });
      continue;
    }
    const { attachment: a, bytes } = part;
    const sha256 = createHash("sha256").update(bytes).digest("hex");
    const identity = createHash("sha256")
      .update(JSON.stringify([a.name, a.mediaType, sha256]))
      .digest("hex");
    const id = "att_" + identity;
    const send = (upload: Record<string, unknown>) =>
      request("attachment/upload", { session_id: sessionId, id, upload });
    const receipt = await send({
      operation: "begin",
      metadata: {
        id,
        name: a.name,
        media_type: a.mediaType,
        size: bytes.length,
        sha256,
      },
    });
    if ((receipt as { sealed?: boolean } | null)?.sealed !== true) {
      for (let offset = 0; offset < bytes.length; offset += CHUNK) {
        if (signal?.aborted)
          throw new HarnessError("Aborted", "Attachment upload cancelled");
        await send({
          operation: "chunk",
          offset,
          data: bytes.subarray(offset, offset + CHUNK).toString("base64"),
        });
      }
      await send({ operation: "commit" });
    }
    result.push({ type: a.type, attachment_id: id });
  }
  if (signal?.aborted)
    throw new HarnessError("Aborted", "Attachment upload cancelled");
  return result;
}
