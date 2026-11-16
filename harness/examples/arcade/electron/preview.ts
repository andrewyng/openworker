import type { Library } from "./library.js";
import { SAVE_CLIENT } from "./save-client.js";
import type { StarterCatalog } from "./starters.js";
import { fileBytes, textFile, assetMime } from "./project.js";
export const PREVIEW_CSP =
  "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src data: blob:; media-src data: blob:; connect-src 'none'; frame-src 'none'; object-src 'none'; form-action 'none'; base-uri 'none'; worker-src 'none'";
// Opaque-origin frames deliberately cannot use persistent browser storage. Offer
// the familiar API backed only by this frame's memory, with no host bridge.
const MEMORY_STORAGE = `<script>(()=>{for(const name of ['localStorage','sessionStorage']){const data=new Map();let size=0;const storage={getItem(key){return data.get(String(key))??null},setItem(key,value){key=String(key);value=String(value);const next=size-(data.has(key)?key.length+data.get(key).length:0)+key.length+value.length;if(next>524288||(!data.has(key)&&data.size>=4096))throw new DOMException('Preview storage limit reached','QuotaExceededError');data.set(key,value);size=next},removeItem(key){key=String(key);if(data.has(key)){size-=key.length+data.get(key).length;data.delete(key)}},clear(){data.clear();size=0},key(index){return [...data.keys()][Number(index)]??null},get length(){return data.size}};Object.defineProperty(window,name,{value:new Proxy(storage,{get(target,key){return key in target?Reflect.get(target,key):typeof key==='string'?target.getItem(key):undefined},set(target,key,value){target.setItem(key,value);return true},deleteProperty(target,key){target.removeItem(key);return true}}),configurable:false})}})();</script>`;
export function previewHtml(html: string, saves = true): string {
  const doctype = html.match(/^\s*<!doctype[^>]*>/i);
  return doctype
    ? doctype[0] +
        MEMORY_STORAGE +
        (saves ? SAVE_CLIENT : "") +
        html.slice(doctype[0].length)
    : "<!doctype html>" + MEMORY_STORAGE + (saves ? SAVE_CLIENT : "") + html;
}
export function previewUrl(gameId: string, revisionId: string): string {
  return `arcade-preview://${gameId}.${revisionId}/index.html`;
}
export function previewResponse(
  library: Library,
  requestUrl: string,
  starters?: StarterCatalog,
): Response {
  try {
    const url = new URL(requestUrl);
    if (
      url.protocol !== "arcade-preview:" ||
      url.username ||
      url.password ||
      url.search ||
      url.hash ||
      /%|\\/.test(url.pathname)
    )
      throw new Error();
    const parts = url.pathname.split("/").filter(Boolean);
    if (
      url.hostname === "games" &&
      parts.length === 2 &&
      parts[1] === "thumbnail.png"
    )
      return new Response(new Uint8Array(library.thumbnailBytes(parts[0])), {
        headers: {
          "Content-Type": "image/png",
          "Cache-Control": "no-store",
          "X-Content-Type-Options": "nosniff",
        },
      });
    const [gameId, revisionId, ...extra] = url.hostname.split(".");
    if (extra.length || !gameId || !revisionId) throw new Error();
    const path = parts.join("/");
    const origin = `arcade-preview://${url.host}`;
    // GLTFLoader's ImageBitmapLoader fetches local Blob URLs for embedded textures.
    // Allow those in-memory bytes, not HTTP(S), workers, or another game's origin.
    const csp = `default-src 'none'; script-src 'unsafe-inline' ${origin}; style-src 'unsafe-inline' ${origin}; img-src data: blob: ${origin}; media-src data: blob: ${origin}; font-src ${origin}; connect-src ${origin} blob:; frame-src 'none'; object-src 'none'; form-action 'none'; base-uri 'none'; worker-src 'none'`;
    const asset = gameId.startsWith("starter-")
      ? starters?.asset(url.host, path)
      : library.asset(gameId, revisionId, path);
    if (asset === undefined) throw new Error();
    return new Response(
      path.toLowerCase().endsWith(".html")
        ? previewHtml(textFile(asset))
        : new Uint8Array(fileBytes(asset)),
      {
        headers: {
          "Content-Type":
            assetMime(path) +
            (typeof asset === "string" ? "; charset=utf-8" : ""),
          "Content-Security-Policy": csp,
          "Access-Control-Allow-Origin": "*",
          "Cache-Control": "no-store",
          "X-Content-Type-Options": "nosniff",
        },
      },
    );
  } catch {
    return new Response(
      "Game content is unavailable. Your saved files have not been changed.",
      {
        status: 404,
        headers: {
          "Content-Type": "text/plain",
          "Content-Security-Policy": PREVIEW_CSP,
        },
      },
    );
  }
}
