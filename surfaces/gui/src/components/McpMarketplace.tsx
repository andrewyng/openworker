import { useState, useEffect } from "react";
import { addMcpServer, getMcpServers } from "../api";
import { Icon } from "./Icon";

export interface McpCatalogItem {
  id: string;
  name: string;
  description: string;
  icon: any; // Using any here to bypass strict typing for now if IconName gets updated
  author: string;
  config: Record<string, any>;
}

export const MCP_CATALOG: McpCatalogItem[] = [
  {
    id: "github",
    name: "GitHub",
    description: "Manage repositories, pull requests, and issues directly through the MCP interface.",
    icon: "branch",
    author: "ModelContextProtocol",
    config: {
      command: "npx",
      args: ["-y", "@modelcontextprotocol/server-github"],
      env: { GITHUB_PERSONAL_ACCESS_TOKEN: "" },
      enabled: true,
    }
  },
  {
    id: "postgres",
    name: "PostgreSQL",
    description: "Read-only access to PostgreSQL databases with schema inspection and query execution.",
    icon: "table",
    author: "ModelContextProtocol",
    config: {
      command: "npx",
      args: ["-y", "@modelcontextprotocol/server-postgres", "postgresql://user:password@localhost/dbname"],
      enabled: true,
    }
  },
  {
    id: "sqlite",
    name: "SQLite",
    description: "Interact with local SQLite databases for analysis and data manipulation.",
    icon: "archive",
    author: "ModelContextProtocol",
    config: {
      command: "uvx",
      args: ["mcp-server-sqlite", "--db-path", "~/test.db"],
      enabled: true,
    }
  },
  {
    id: "slack",
    name: "Slack",
    description: "Send messages, read channels, and interact with Slack workspaces.",
    icon: "chat",
    author: "ModelContextProtocol",
    config: {
      command: "npx",
      args: ["-y", "@modelcontextprotocol/server-slack"],
      env: { SLACK_BOT_TOKEN: "", SLACK_TEAM_ID: "" },
      enabled: true,
    }
  },
  {
    id: "brave-search",
    name: "Brave Search",
    description: "Perform web searches using the Brave Search API to retrieve live information.",
    icon: "search",
    author: "ModelContextProtocol",
    config: {
      command: "npx",
      args: ["-y", "@modelcontextprotocol/server-brave-search"],
      env: { BRAVE_API_KEY: "" },
      enabled: true,
    }
  },
  {
    id: "puppeteer",
    name: "Puppeteer",
    description: "Browser automation for scraping and interacting with dynamic websites.",
    icon: "code",
    author: "ModelContextProtocol",
    config: {
      command: "npx",
      args: ["-y", "@modelcontextprotocol/server-puppeteer"],
      enabled: true,
    }
  },
  {
    id: "google-maps",
    name: "Google Maps",
    description: "Interact with Google Maps API for location data, routing, and places.",
    icon: "pin",
    author: "ModelContextProtocol",
    config: {
      command: "npx",
      args: ["-y", "@modelcontextprotocol/server-google-maps"],
      env: { GOOGLE_MAPS_API_KEY: "" },
      enabled: true,
    }
  },
];

export function McpMarketplace() {
  const [installed, setInstalled] = useState<Set<string>>(new Set());
  const [installing, setInstalling] = useState<Set<string>>(new Set());

  // Load existing servers to mark them as installed
  useEffect(() => {
    getMcpServers().then((servers) => {
      const names = new Set(servers.map((s) => s.name));
      setInstalled(names);
    }).catch(console.error);
  }, []);

  const handleInstall = async (item: McpCatalogItem) => {
    setInstalling(prev => new Set(prev).add(item.id));
    try {
      await addMcpServer(item.id, item.config);
      setInstalled(prev => new Set(prev).add(item.id));
    } catch (e) {
      console.error("Failed to install", e);
      alert("Failed to install: " + String(e));
    } finally {
      setInstalling(prev => {
        const next = new Set(prev);
        next.delete(item.id);
        return next;
      });
    }
  };

  return (
    <div className="grid grid-cols-1 md:grid-cols-2 gap-4 pb-12 mt-2">
      {MCP_CATALOG.map((item) => {
        const isInstalled = installed.has(item.id);
        const isInstalling = installing.has(item.id);
        return (
          <div key={item.id} className="rounded-xl2 border border-line bg-panel p-5 flex flex-col hover:border-lineStrong transition-colors">
            <div className="flex items-start justify-between mb-3">
              <div className="flex items-center gap-3">
                <div className="w-10 h-10 rounded-lg bg-paper border border-line flex items-center justify-center shrink-0">
                  <Icon name={item.icon as any} size={18} className="text-muted" />
                </div>
                <div>
                  <h3 className="text-[14px] font-semibold text-ink leading-tight">{item.name}</h3>
                  <div className="text-[11px] text-faint mt-0.5">By {item.author}</div>
                </div>
              </div>
              <button
                className={`text-[12.5px] px-3 py-1.5 rounded-lg font-medium transition-colors border ${
                  isInstalled 
                    ? "bg-paper text-accent border-line cursor-default" 
                    : isInstalling 
                      ? "bg-accent/50 text-white cursor-wait border-transparent"
                      : "bg-accent text-white hover:bg-accent/90 border-transparent shadow-sm"
                }`}
                disabled={isInstalled || isInstalling}
                onClick={() => handleInstall(item)}
              >
                {isInstalled ? "Installed" : isInstalling ? "Installing..." : "Install"}
              </button>
            </div>
            <p className="text-[13px] text-muted leading-relaxed flex-1">
              {item.description}
            </p>
          </div>
        );
      })}
    </div>
  );
}
