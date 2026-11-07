import type { DesktopBridge } from "../shared/contracts";
declare global {
  interface Window {
    arcade: DesktopBridge;
  }
}
export const bridge = window.arcade;
