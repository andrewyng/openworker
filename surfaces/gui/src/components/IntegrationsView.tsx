// The standalone Connectors surface is RETIRED (owner 2026-08-31): Connectors
// lives in Settings under the MACHINE group — machine-scoped like every other
// engine page (see SettingsView + RemoteConnectorsPanel). This file keeps
// PanelHead, the page-shell heading shared across surfaces.

export function PanelHead({ title, sub }: { title: string; sub: string }) {
  return (
    <div className="mb-4">
      <h2 className="text-title font-semibold tracking-tight">{title}</h2>
      <p className="text-ui text-muted mt-0.5">{sub}</p>
    </div>
  );
}
