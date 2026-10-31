// Read-only, bounded ingress-stop observation. An empty initial list is success;
// an unavailable Kubernetes API is not proof that the pods disappeared.
export async function waitForEmptyPods(
  list,
  { timeoutMs = 90000, intervalMs = 250 } = {},
) {
  const until = Date.now() + timeoutMs;
  while (true) {
    const result = await list();
    if (!Array.isArray(result.items)) throw Error("Invalid pod-list response");
    if (result.items.length === 0) return;
    if (Date.now() >= until)
      throw Error("Ingress pods did not disappear before cleanup deadline");
    await new Promise((r) => setTimeout(r, intervalMs));
  }
}
