import sys, asyncio, json
from mcp import ClientSession
from mcp.client.stdio import stdio_client, StdioServerParameters

async def main(query, max_results=5):
    srv = StdioServerParameters(command="npx", args=["-y", "tavily-mcp@latest"])
    async with stdio_client(srv) as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()
            res = await s.call_tool("tavily_search", {"query": query, "max_results": max_results, "days": 21, "search_depth": "advanced"})
            for c in res.content:
                if getattr(c, "type", None) == "text":
                    print(c.text[:3500])

asyncio.run(main(sys.argv[1], int(sys.argv[2]) if len(sys.argv) > 2 else 5))
