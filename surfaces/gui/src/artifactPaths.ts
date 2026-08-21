type ArtifactPath = { path: string; abs_path?: string; name: string };

export function decodeArtifactPath(path: string): string {
  try {
    return decodeURIComponent(path);
  } catch {
    // A literal percent in a filename is valid even when it is not URL encoding.
    return path;
  }
}

export function normalizeArtifactPath(path: string): string {
  return decodeArtifactPath(path).replace(/\\/g, "/").replace(/^\.\/+/, "");
}

export function artifactBaseName(path: string): string {
  const normalized = normalizeArtifactPath(path);
  return normalized.split("/").pop() || normalized;
}

export function findArtifact<T extends ArtifactPath>(list: T[], path: string): T | undefined {
  const requested = normalizeArtifactPath(path);
  const bareName = !requested.includes("/");
  return list.find((artifact) => {
    const candidates = [artifact.path, artifact.abs_path].filter(Boolean) as string[];
    if (
      candidates.some((candidate) => {
        const normalized = normalizeArtifactPath(candidate);
        return normalized === requested || normalized.endsWith("/" + requested);
      })
    ) {
      return true;
    }
    return bareName && normalizeArtifactPath(artifact.name) === requested;
  });
}
