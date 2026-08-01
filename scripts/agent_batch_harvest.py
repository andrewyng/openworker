import argparse
import os
import subprocess
from pathlib import Path
import concurrent.futures

try:
    import dotenv
    dotenv.load_dotenv(Path(__file__).parent.parent / ".env", override=True)
    if os.environ.get("GEMINI_API_KEY") and "GOOGLE_API_KEY" in os.environ:
        del os.environ["GOOGLE_API_KEY"]
except ImportError:
    pass

def main():
    parser = argparse.ArgumentParser(description="Agentic Batch Harvester")
    parser.add_argument("directory", nargs='?', default="/Users/jose/Library/CloudStorage/OneDrive-SharedLibraries-Onedrive/AI_Master-2023", help="Directory to scan")
    parser.add_argument("--local", action="store_true", help="Use local LLM on port 8000")
    parser.add_argument("--model", type=str, default=None, help="Model string (e.g. openai:gpt-4o, or Qwen3.6-35B-A3B-6bit)")
    parser.add_argument("--concurrency", type=int, default=5, help="Number of files to harvest in parallel")
    parser.add_argument("--vision", action="store_true", help="Use vision model for PDFs")
    args = parser.parse_args()

    read_only_root = Path("/Users/jose/Library/CloudStorage/OneDrive-SharedLibraries-Onedrive/AI_Master-2023")
    project_root = Path("/Users/jose/OpenWorker/7fcac218-bd1")  # write-allowed base
    repo_root = Path(__file__).parent.parent.resolve()          # scripts and venv base
    target_dir = Path(args.directory)
    if not target_dir.exists():
        print(f"Error: {target_dir} does not exist")
        return

    print(f"Scanning {target_dir} for harvestable files...")

    tasks = []

    for root, dirs, files in os.walk(target_dir):
        # Detect read-only restrictions
        if Path(root).resolve().is_relative_to(read_only_root):
            print(f"[Info] Scanning {root}, read-only mode...")
        
        # Modify dirs in-place to skip hidden folders, venvs, and node_modules
        try:
            dirs[:] = [
                d for d in dirs 
                if not (
                    d.startswith(".") 
                    or "venv" in d.lower() 
                    or d.lower() in ("node_modules", "__pycache__", "env", "lib", "include", "bin", "sent", "dev", "exporttohtml", "0_books")
                )
            ]
        except PermissionError:
            print(f"[Warning] Permission denied for directory listing in {root}")

        for file in files:
            ext = Path(file).suffix.lower()
            if ext in [".pdf"]:
                file_path = Path(root) / file

                # Skip files larger than 8MB (mostly massive reference textbooks/guides)
                if file_path.stat().st_size > 8 * 1024 * 1024:
                    print(f"Skipping {file_path.name} (File size exceeds 8MB limit, likely a textbook)")
                    continue

                # Create a relative output path to mimic the structure
                rel_path = file_path.relative_to(target_dir)
                out_name = f"{file_path.stem}.md"
                out_path = project_root / "knowledge_packs" / target_dir.name / rel_path.parent / out_name

                if out_path.exists():
                    print(f"Skipping {file_path.name} (Already harvested at {out_path})")
                    continue

                tasks.append((file_path, out_path, ext))

    if not tasks:
        print("No new files found to harvest.")
        return

    print(f"\nFound {len(tasks)} files to harvest. Starting parallel execution (concurrency={args.concurrency})...")

    def run_harvest(task):
        file_path, out_path, ext = task
        print(f"[Harvester] Starting: {file_path.name}")
        cmd = [
            "uv", "run", "scripts/harvester.py",
            str(file_path),
            "--output", str(out_path),
            "--auto"
        ]
        if ext == ".pdf" and args.vision:
            cmd.append("--vision")
        if args.local:
            cmd.append("--local")
        if args.model:
            cmd.extend(["--model", args.model])

        env = os.environ.copy()
        if args.local and "LOCAL_LLM_API_KEY" not in env:
            env["LOCAL_LLM_API_KEY"] = "local-llm"

        try:
            subprocess.run(cmd, check=True, env=env, cwd=str(repo_root))
            print(f"[Harvester] Success: {file_path} harvested to {out_path}")
            return out_path
        except subprocess.CalledProcessError as e:
            print(f"[Harvester] Failed: {file_path.name}: {e}")
            return None

    harvested_files = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.concurrency) as executor:
        results = executor.map(run_harvest, tasks)
        for r in results:
            if r:
                harvested_files.append(r)

    print("\n" + "="*50)
    print(f"Harvested {len(harvested_files)} files.")
    print("="*50)

    if harvested_files:
        print("\nTriggering REM Memory Consolidation on newly harvested packs...")
        cmd = ["uv", "run", "scripts/rem_memory.py", "--all", "--auto"]
        if args.local:
            cmd.append("--local")
        if args.model:
            cmd.extend(["--model", args.model])
            
        env = os.environ.copy()
        if args.local and "LOCAL_LLM_API_KEY" not in env:
            env["LOCAL_LLM_API_KEY"] = "local-llm"
        try:
            subprocess.run(cmd, env=env, cwd=str(repo_root))
        except Exception as e:
            print(f"[Error] REM Memory Consolidation failed: {e}")        
    
    print("\nBatch Ingestion Complete in /Users/jose/ExpertAIAgents/openworker.")
 
if __name__ == "__main__":
    main()
