import os
import argparse
from pathlib import Path
import aisuite as ai
import re

try:
    import dotenv
    dotenv.load_dotenv(Path(__file__).parent.parent / ".env", override=True)
except ImportError:
    pass

try:
    from ddgs import DDGS
except ImportError:
    try:
        from duckduckgo_search import DDGS
    except ImportError:
        DDGS = None

SYSTEM_PROMPT = """<role>You are the REM Memory Subconscious for an AI Expert System.</role>

<instructions>
Your job is to read an existing Expert Pack, compare it against recent Live Web Search Results, and output a CONSOLIDATED markdown file.

Follow these strict rules:
1. RATIONALIZE: If the web search provides newer state-of-the-art methods that contradict the old pack, update the pack to reflect the new truth (e.g. "We used to do X, but now Y is preferred").
2. NORMALIZE: Ensure the final output perfectly matches the standard Expert Pack v4.1 format (YAML frontmatter, Code Examples, Anti-patterns, External References).
3. REM CONSOLIDATION NOTES: You MUST append a new section at the very bottom titled "## REM Consolidation Notes". In this section, write 2-3 bullet points summarizing what you learned from the web search and what you updated in the pack.
4. Provide your strictly formatted markdown output inside <expert_pack> tags. Do not include any conversational preamble.
</instructions>
"""

def search_web(query: str, max_results: int = 3) -> str:
    if DDGS is None:
        return "Web search unavailable (install duckduckgo-search)."
    
    try:
        results = DDGS().text(query, max_results=max_results)
        context = ""
        for r in results:
            context += f"Source: {r.get('title')}\nSnippet: {r.get('body')}\n\n"
        return context
    except Exception as e:
        return f"Web search failed: {e}"

def consolidate_memory(file_path: str, auto: bool = False, use_local: bool = True, model_name: str = None):
    path = Path(file_path)
    if not path.exists():
        print(f"Error: {file_path} not found.")
        return
        
    try:
        if use_local:
            from openai import OpenAI
            import os
            client = OpenAI(
                base_url="http://127.0.0.1:8000/v1",
                api_key=os.getenv("LOCAL_LLM_API_KEY", "local-llm")
            )
            model = model_name or "Qwen3.6-35B-A3B-6bit"
        else:
            import os
            client = ai.Client()
            if model_name and ":" in model_name:
                model = model_name
            elif model_name:
                if model_name.startswith("gpt"):
                    model = f"openai:{model_name}"
                elif model_name.startswith("claude"):
                    model = f"anthropic:{model_name}"
                else:
                    model = f"openai:{model_name}"
            elif os.getenv("OPENAI_API_KEY"):
                model = "openai:gpt-4o"
            elif os.getenv("ANTHROPIC_API_KEY"):
                model = "anthropic:claude-3-5-sonnet-20240620"
            else:
                raise ValueError("No API keys found for cloud models (ANTHROPIC_API_KEY or OPENAI_API_KEY). Use --local.")
            
        original_content = path.read_text()
        
        # 1. Extract concept for web search (from title or filename)
        concept_name = path.stem.replace("_", " ")
        print(f"[REM] Searching web for latest context on: {concept_name}...")
        search_context = search_web(f"latest state of the art {concept_name} machine learning 2024")
        
        # 2. Run the LLM reflection
        print(f"[REM] Consolidating memory with LLM using {model}...")
        
        prompt = f"<input>\nOriginal Expert Pack:\n{original_content}\n\nLive Search Results for Context:\n{search_context}\n</input>"
        
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": prompt}
            ],
            temperature=0.3,
        )
        
        out = response.choices[0].message.content
        
        # Extract from XML tag if Claude provided it
        match = re.search(r"<expert_pack>(.*?)</expert_pack>", out, re.DOTALL)
        if match:
            out = match.group(1).strip()
        
        if out.startswith("```markdown"):
            out = out[11:]
        if out.startswith("```"):
            out = out[3:]
        if out.endswith("```"):
            out = out[:-3]
            
        final_content = out.strip()
        
        if auto:
            path.write_text(final_content)
            print(f"[REM] Automatically consolidated {path.name}")
        else:
            # Interactive Review
            draft_path = Path("drafts") / f"rem_{path.name}"
            draft_path.parent.mkdir(parents=True, exist_ok=True)
            draft_path.write_text(final_content)
            
            print("\n" + "="*50)
            print("REM MEMORY CONSOLIDATION DRAFT GENERATED!")
            print(f"Preview saved to: {draft_path}")
            print("="*50 + "\n")
            
            resp = input(f"Review the draft above. Overwrite {path.name} with these updates? (y/n): ")
            if resp.lower().startswith('y'):
                path.write_text(final_content)
                draft_path.unlink()
                print(f"[REM] Successfully updated {path.name}")
            else:
                print("[REM] Aborted. Original file preserved. Draft remains in drafts folder.")
    except Exception as e:
        print(f"Error during consolidation: {e}")

def main():
    parser = argparse.ArgumentParser(description="REM Memory Background Consolidator")
    parser.add_argument("file_path", nargs="?", help="Specific Expert Pack to consolidate")
    parser.add_argument("--all", action="store_true", help="Consolidate all packs in knowledge_packs/")
    parser.add_argument("--auto", action="store_true", help="Skip interactive review")
    parser.add_argument("--cloud", action="store_true", help="Use cloud LLM instead of local LLM")
    parser.add_argument("--local", action="store_true", help="Use local LLM on port 8000 (default)")
    parser.add_argument("--model", type=str, default=None, help="Model string (e.g. openai:gpt-4o, or Qwen3.6-35B-A3B-6bit)")
    
    args = parser.parse_args()
    
    # Default to local unless --cloud is explicitly requested
    use_local = not args.cloud
    
    if args.all:
        kp_dir = Path("knowledge_packs")
        for file in kp_dir.rglob("*.md"):
            if file.is_relative_to(kp_dir / "admin"):
                continue
            consolidate_memory(str(file), auto=args.auto, use_local=use_local, model_name=args.model)
    elif args.file_path:
        consolidate_memory(args.file_path, auto=args.auto, use_local=use_local, model_name=args.model)
    else:
        print("Please provide a file_path or --all")

if __name__ == "__main__":
    main()
