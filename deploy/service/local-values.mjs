// Public Helm configuration only. Credentials are separate Kubernetes Secrets.
export function localValues(input, m) {
  const config = structuredClone(input);
  config.runner = "/opt/openworker/openworker";
  config.openshell.image = m.runnerImage;
  return {
    image: m.imageRef,
    config,
    secrets: { model: config.scripted === false ? "ow-service-model" : "" },
    worker: { ownership: "manual", maxReplicas: 4, slots: 2, drainSeconds: 30 },
    terminationGracePeriodSeconds: 90,
  };
}
