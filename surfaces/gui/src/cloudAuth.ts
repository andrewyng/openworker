// Hosted-dashboard sign-in gate (remote-home-design.md §Cloud dashboard).
//
// Runs BEFORE React renders. The backend's /v1/capabilities says whether this
// deployment wants a login (server-driven `auth` block — the bundle itself
// carries no tenant identifiers). When it does, the Auth0 access token becomes
// the app-wide API token: the same global the desktop shell injects, so every
// existing fetch/WebSocket authenticates with zero per-endpoint changes.
//
// Desktop and the OSS acceptor skeleton advertise no `auth` block and skip
// all of this — the gate resolves immediately.

import { getActiveOrg, getMe, httpBase, setActiveOrg, type MeInfo } from "./api";

const ORG_STORAGE_KEY = "ocw.cloud.org";

let me: MeInfo | null = null;
let signOutImpl: (() => Promise<void>) | null = null;

/** Identity + memberships resolved at boot (hosted cloud only). */
export function cloudMe(): MeInfo | null {
  return me;
}

export function canSignOut(): boolean {
  return signOutImpl !== null;
}

/** Org policy (OPE-150): may this browser push keys in the active org? The
 * API refuses regardless — this only decides whether to OFFER the button. */
export function keyPushAllowed(): boolean {
  return me?.policies?.key_push !== false;
}

export async function cloudSignOut(): Promise<void> {
  if (signOutImpl) await signOutImpl();
}

// switchOrg removed 2026-08-31 (spec §"One login, one org"): the org is a
// function of the signed-in identity; the stored-org resolution below stays
// only to honor an already-persisted selection until it ages out.

async function resolveOrg(): Promise<void> {
  try {
    me = await getMe();
  } catch {
    return; // backend without /v1/me (skeleton) — org-less single tenant
  }
  let stored = "";
  try {
    stored = localStorage.getItem(ORG_STORAGE_KEY) || "";
  } catch {
    /* no storage */
  }
  const valid = new Set(me.orgs.map((o) => o.id));
  setActiveOrg(stored && valid.has(stored) ? stored : me.org_id);
  if (getActiveOrg() !== me.org_id) {
    // Re-read under the selected org so `me.org_id` mirrors what every
    // subsequent request will resolve to.
    try {
      me = await getMe();
    } catch {
      /* keep the boot read */
    }
  }
}

/** Whether boot should wait on this gate at all. Hosted dashboards are the
 * only https deployments; desktop (tauri://, http loopback) and dev servers
 * render synchronously, exactly as before the gate existed. */
export function shouldGate(): boolean {
  if ((import.meta as any).env?.VITE_CLOUD_AUTH === "1") return true; // dev opt-in
  return typeof location !== "undefined" && location.protocol === "https:";
}

/** Resolve once the app may render. Never resolves when a login redirect is
 * underway — the navigation replaces the page. */
export async function initCloudAuth(): Promise<void> {
  if ((globalThis as any).__COWORKER_API_TOKEN__) {
    return; // shell-injected token (desktop) wins; nothing to do
  }
  let caps: { mode?: string; auth?: { kind: string; domain: string; client_id: string; audience: string } };
  try {
    caps = await (await globalThis.fetch(`${httpBase()}/v1/capabilities`)).json();
  } catch {
    return; // no backend yet (desktop boot race) — the app has its own retry
  }
  if (caps?.mode !== "cloud" || caps?.auth?.kind !== "auth0") return;

  const { createAuth0Client } = await import("@auth0/auth0-spa-js");
  const auth0 = await createAuth0Client({
    domain: caps.auth.domain,
    clientId: caps.auth.client_id,
    cacheLocation: "localstorage",
    authorizationParams: {
      audience: caps.auth.audience,
      redirect_uri: window.location.origin,
    },
  });

  const params = new URLSearchParams(window.location.search);
  if (params.has("code") && params.has("state")) {
    try {
      await auth0.handleRedirectCallback();
    } catch {
      /* stale state (reload of an old callback URL) — fall through */
    }
    window.history.replaceState({}, "", window.location.pathname);
  }

  try {
    const token = await auth0.getTokenSilently();
    (globalThis as any).__COWORKER_API_TOKEN__ = token;
  } catch {
    await auth0.loginWithRedirect();
    await new Promise<never>(() => {}); // navigating away — never render
  }

  signOutImpl = async () => {
    try {
      localStorage.removeItem(ORG_STORAGE_KEY);
    } catch {
      /* no storage */
    }
    await auth0.logout({ logoutParams: { returnTo: window.location.origin } });
  };
  await resolveOrg();
}
