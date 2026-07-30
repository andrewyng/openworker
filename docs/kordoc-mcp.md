# Kordoc RAG-ready parser

OpenWorker's **Korean Document Coworker** can use a locally installed Kordoc MCP
server to analyze one Korean office document at a time. This integration prepares
structured chunks for the current conversation; it is not a persistent RAG index.

## Prerequisites

- Node.js with npm
- Exact Kordoc version `4.2.3`

Install the pinned runtime explicitly:

```powershell
npm install --global kordoc@4.2.3
```

OpenWorker discovers the canonical Node executable, asks npm for its global package
root through npm's JavaScript entrypoint, verifies the exact package version, and
starts `dist/mcp.js` directly. It does not use `npx`, a shell command string, or
Kordoc's setup wizard, and it does not modify another MCP client's configuration.

## Read-only tool surface

The persona exposes only these Kordoc 4.2.3 tools:

- `detect_format`
- `parse_metadata`
- `parse_chunks`
- `parse_pages`
- `parse_table`

All other Kordoc tools remain unavailable. In particular, the integration does not
fill or patch forms, generate or render documents, place seals, redact files, or
write an index.

For `parse_metadata`, OpenWorker cross-checks Kordoc 4.2.3's reported format with
the same bound server's `detect_format` result. This corrects the upstream generic
ZIP label for DOCX/XLSX without trusting file extensions; if verification is
unavailable or malformed, the original metadata response is preserved.

Each tool is marked approval-required. Prompt frequency still follows the active
permission mode and any explicit session grant the user chooses. The source must be
an existing regular file in the session workspace. OpenWorker resolves accepted
paths to canonical absolute paths before dispatch and rejects traversal, missing
files, directories, and symlink or junction escapes. A user-configured MCP server
also named `kordoc` cannot replace the pinned server used by this persona: the
built-in runtime has a private connection key that is never loaded from user
`mcp.json` settings, while its model-facing tool namespace remains `kordoc`.

## RAG boundary

`parse_chunks` preserves heading and Korean outline breadcrumbs and returns tables
as structured chunks. OpenWorker passes those chunks to the active model so it can
answer questions in the current conversation.

This first integration does **not** create embeddings, a vector database, citations
backed by a durable corpus, folder watches, or background ingestion. Chunk sizing,
overlap, long-term indexing, and cross-document retrieval remain future work. Any
document content sent to the configured model is subject to that model provider's
data handling terms.

Supported parsing depends on the pinned Kordoc runtime and its installed optional
components. The intended core inputs are HWP, HWPX, PDF, XLS/XLSX, and DOCX files.
See the [Kordoc project](https://github.com/chrisryugj/kordoc) for format-specific
limitations.
