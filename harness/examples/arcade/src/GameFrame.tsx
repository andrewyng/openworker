import React, { useEffect, useRef } from "react";
import { validateProgressRequest } from "../shared/game-progress";
// The parent owns the scope. Game messages cannot supply a game ID, path or URL.
export function GameFrame(
  props: React.IframeHTMLAttributes<HTMLIFrameElement> & { src: string },
) {
  const cleanup = useRef<() => void>(() => {});
  useEffect(() => () => cleanup.current(), []);
  return (
    <iframe
      {...props}
      onLoad={(e) => {
        cleanup.current();
        const frame = e.currentTarget,
          channel = new MessageChannel();
        let live = true,
          inFlight = 0,
          count = 0,
          windowStart = Date.now();
        cleanup.current = () => {
          live = false;
          channel.port1.close();
        };
        channel.port1.onmessage = async (event) => {
          const data = event.data;
          if (!live || !data || !Number.isSafeInteger(data.id)) return;
          try {
            if (Date.now() - windowStart > 10000) {
              count = 0;
              windowStart = Date.now();
            }
            if (++count > 60 || inFlight >= 4)
              throw new Error("Save request rate exceeded.");
            const request = validateProgressRequest(data.request);
            inFlight++;
            try {
              const result = await window.arcade.gameProgress(
                props.src,
                request,
              );
              if (live)
                channel.port1.postMessage({ id: data.id, ok: true, result });
            } finally {
              inFlight--;
            }
          } catch (error) {
            if (live)
              channel.port1.postMessage({
                id: data.id,
                ok: false,
                error: error instanceof Error ? error.message : "Save failed.",
              });
          }
        };
        frame.contentWindow?.postMessage({ type: "arcade.save.connect" }, "*", [
          channel.port2,
        ]);
        props.onLoad?.(e);
      }}
    />
  );
}
