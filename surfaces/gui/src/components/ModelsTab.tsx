import { useEffect, useState } from "react";
import {
  getSettings,
  removeModel,
  setDefaultModel,
  type ModelSettings,
  type ProviderInfo,
} from "../api";
import { ModelChecklist } from "./ModelChecklist";
import {
  ProviderCards,
  ProviderForm,
  useProviderSetup,
} from "../providers/ProviderSetup";

const SEC_H =
  "text-[11px] uppercase tracking-[0.05em] text-faint font-semibold";

type ProviderSetupState = ReturnType<typeof useProviderSetup>;

export function ModelsTab() {
  const [settings, setSettings] = useState<ModelSettings | null>(null);
  const refreshSettings = () =>
    getSettings()
      .then(setSettings)
      .catch(() => setSettings(null));
  const ps = useProviderSetup({ onSaved: refreshSettings });

  useEffect(() => {
    refreshSettings();
  }, []);

  if (!settings) {
    return <div className="text-[13px] text-muted">Loading…</div>;
  }
  if (ps.sel === null) {
    return (
      <div>
        <ProviderCards
          ps={ps}
          tp="set"
          gridClass="grid grid-cols-2 xl:grid-cols-3 gap-2.5"
          lastUsed
        />
        <ComposerPickerCard
          settings={settings}
          providers={ps.providers}
          onChanged={refreshSettings}
        />
      </div>
    );
  }

  return (
    <ProviderSettings
      ps={ps}
      settings={settings}
      onModelsChanged={(models, model) =>
        setSettings((current) =>
          current ? { ...current, models, model } : current,
        )
      }
    />
  );
}

function RemoveKeyButton({ ps }: { ps: ProviderSetupState }) {
  if (!ps.credentialed) return null;
  const remove = () => {
    if (
      window.confirm(`Remove the ${ps.info?.title} key from this computer?`)
    ) {
      ps.removeKey();
    }
  };
  return (
    <button
      className="text-[12.5px] text-danger/80 hover:text-danger hover:underline underline-offset-2"
      data-testid="set-remove-key"
      onClick={remove}
    >
      Remove key…
    </button>
  );
}

function EnvironmentKeyNotice({
  provider,
  source,
}: {
  provider: string;
  source: string | null;
}) {
  if (provider !== "openai" || source !== "env") return null;
  return (
    <p className="text-[12px] text-muted mt-3 leading-relaxed">
      A key is set via <code>OPENAI_API_KEY</code> in this server's environment.
      You can override it above; the stored key is used only when the
      environment variable is absent.
    </p>
  );
}

function ConfiguredModels({
  ps,
  settings,
  onChanged,
}: {
  ps: ProviderSetupState;
  settings: ModelSettings;
  onChanged: (models: string[], model: string) => void;
}) {
  if (!ps.sel) return null;
  return (
    <div className="mt-6">
      <div className={SEC_H + " mb-1.5"}>Models</div>
      <p className="text-[12px] text-muted mb-2.5 leading-relaxed">
        Ticked models show in the composer's picker; the black badge marks the
        default for new sessions.
      </p>
      <ModelChecklist
        provider={ps.sel}
        knownProviders={ps.providers.map((provider) => provider.name)}
        suggested={ps.info?.suggested_models || []}
        curated={settings.models}
        defaultModel={settings.model}
        labels={settings.model_labels}
        onChanged={(next) => onChanged(next.models, next.model)}
      />
    </div>
  );
}

function SuggestedModels({
  provider,
  settings,
  models,
}: {
  provider: string;
  settings: ModelSettings;
  models: string[];
}) {
  if (!models.length) return null;
  return (
    <div className="mt-6" data-testid="model-preview">
      <div className={SEC_H + " mb-1.5"}>Included models</div>
      <p className="text-[12px] text-muted mb-2.5 leading-relaxed">
        Curated, agent-capable models this provider serves — add your key above
        to enable them.
      </p>
      <div className="space-y-1">
        {models.map((model) => {
          const full = provider === "openai" ? model : `${provider}:${model}`;
          return (
            <div
              key={model}
              className="px-2.5 py-1.5 rounded-lg border border-line bg-paper text-[13px] text-muted"
              title={full}
            >
              {settings.model_labels?.[full] || model}
            </div>
          );
        })}
      </div>
    </div>
  );
}

function ProviderSettings({
  ps,
  settings,
  onModelsChanged,
}: {
  ps: ProviderSetupState;
  settings: ModelSettings;
  onModelsChanged: (models: string[], model: string) => void;
}) {
  const provider = ps.sel;
  if (!provider) return null;
  const models = ps.info?.suggested_models || [];
  return (
    <div>
      <ProviderForm ps={ps} tp="set" footer={<RemoveKeyButton ps={ps} />} />
      <EnvironmentKeyNotice provider={provider} source={settings.source} />
      {ps.info?.configured ? (
        <ConfiguredModels
          ps={ps}
          settings={settings}
          onChanged={onModelsChanged}
        />
      ) : (
        <SuggestedModels
          provider={provider}
          settings={settings}
          models={models}
        />
      )}
    </div>
  );
}

// The gallery view shows every curated model across providers with its provider tag.
function ComposerPickerCard({
  settings,
  providers,
  onChanged,
}: {
  settings: ModelSettings;
  providers: ProviderInfo[];
  onChanged: () => void;
}) {
  const names = providers.map((provider) => provider.name);
  const providerOf = (id: string) => {
    const separator = id.indexOf(":");
    return separator > 0 && names.includes(id.slice(0, separator))
      ? id.slice(0, separator)
      : "openai";
  };
  const providerTag = (id: string) => {
    const provider = providers.find((item) => item.name === providerOf(id));
    return (provider?.title || providerOf(id)).split(" (")[0];
  };
  return (
    <div className="mt-6" data-testid="composer-picker">
      <div className={SEC_H + " mb-1.5"}>In the composer's picker</div>
      <p className="text-[12px] text-muted mb-2.5 leading-relaxed">
        The models offered when starting a session; the black badge marks the
        default. Add more from a provider's card above.
      </p>
      <div className="mlist">
        {settings.models.map((id) => {
          const isDefault = id === settings.model;
          return (
            <div className="mlist-row" key={id}>
              <label className="mlist-main">
                <input
                  type="checkbox"
                  checked
                  disabled={isDefault}
                  title={
                    isDefault
                      ? "The default model is always shown — make another model default first"
                      : "Remove from the picker"
                  }
                  onChange={() =>
                    removeModel(id).then((result) => result.ok && onChanged())
                  }
                />
                <span className="mlist-name" title={id}>
                  {settings.model_labels?.[id] || id}
                </span>
              </label>
              <span className="text-[11px] text-faint mr-2 shrink-0">
                {providerTag(id)}
              </span>
              {isDefault ? (
                <span className="mlist-default">default</span>
              ) : (
                <button
                  className="mlist-make"
                  onClick={() => setDefaultModel(id).then(onChanged)}
                >
                  Make default
                </button>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
