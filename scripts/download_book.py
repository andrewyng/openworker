import os
import sys
import argparse
import urllib.request
import re
from pathlib import Path

# Load environment variables if present
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

# Standard browser User-Agent to prevent basic request blocks
USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"

def download_file(url: str, output_path: Path) -> bool:
    print(f"Attempting to download: {url}")
    try:
        req = urllib.request.Request(
            url, 
            headers={"User-Agent": USER_AGENT}
        )
        
        # Open URL and inspect headers
        with urllib.request.urlopen(req, timeout=30) as response:
            content_type = response.headers.get("Content-Type", "")
            
            # Verify it is actually a PDF (or close enough)
            if "application/pdf" not in content_type.lower() and not url.lower().endswith(".pdf"):
                print(f"[Skip] Content-Type is '{content_type}', not a PDF.")
                return False
            
            # Ensure output folder exists
            output_path.parent.mkdir(parents=True, exist_ok=True)
            
            # Write file in chunks
            with open(output_path, "wb") as f:
                block_size = 8192
                while True:
                    buffer = response.read(block_size)
                    if not buffer:
                        break
                    f.write(buffer)
            
            print(f"[Success] Downloaded to: {output_path}")
            return True
            
    except Exception as e:
        print(f"[Error] Failed to download {url}: {e}")
        return False

def search_and_download(query: str, output_dir: Path, custom_filename: str = None) -> Path:
    # 1. Check if query is a direct URL
    if query.startswith("http://") or query.startswith("https://"):
        filename = custom_filename or url_to_filename(query)
        out_path = output_dir / filename
        if download_file(query, out_path):
            return out_path
        return None
        
    # 2. Search for PDF
    if DDGS is None:
        print("[Error] duckduckgo-search package not found. Install it with: uv pip install duckduckgo-search")
        return None
        
    # Format query to target PDFs
    search_query = f"{query} pdf"
    print(f"Searching web for: '{search_query}'...")
    
    try:
        with DDGS() as ddgs:
            results = list(ddgs.text(search_query, max_results=5))
    except Exception as e:
        print(f"[Error] Search failed: {e}")
        return None
        
    if not results:
        print("[Info] No search results found.")
        return None
        
    # Extract links that look like PDFs first, then try others
    links = [r.get("href") for r in results if r.get("href")]
    
    # Prioritize links ending in .pdf
    pdf_links = [l for l in links if l.lower().endswith(".pdf")]
    other_links = [l for l in links if not l.lower().endswith(".pdf")]
    candidate_links = pdf_links + other_links
    
    filename = custom_filename or clean_query_to_filename(query)
    out_path = output_dir / filename
    
    for url in candidate_links:
        if download_file(url, out_path):
            return out_path
            
    print("[Error] Failed to download textbook from any candidate link.")
    return None

def url_to_filename(url: str) -> str:
    path = Path(url.split("?")[0])
    name = path.name
    if not name.lower().endswith(".pdf"):
        name = f"{name}.pdf"
    return name

def clean_query_to_filename(query: str) -> str:
    clean = re.sub(r"[^a-zA-Z0-9\s_-]", "", query)
    clean = clean.strip().replace(" ", "_").lower()
    return f"{clean}.pdf"

def main():
    parser = argparse.ArgumentParser(description="Open-Access Textbook Downloader for Agents")
    parser.add_argument("query", help="Textbook title / search query OR a direct HTTPS URL to a PDF")
    parser.add_argument("--output-dir", default="knowledge_packs/downloads", help="Directory to save downloaded files")
    parser.add_argument("--filename", default=None, help="Force a custom filename (e.g. intro_stats.pdf)")
    
    args = parser.parse_args()
    
    output_dir = Path(args.output_dir)
    downloaded_path = search_and_download(args.query, output_dir, args.filename)
    
    if downloaded_path and downloaded_path.exists():
        print(f"\n==========================================")
        print(f"TEXTBOOK READY FOR HARVESTING!")
        print(f"Path: {downloaded_path}")
        print(f"==========================================")
        print(f"You can now run:")
        print(f"  uv run scripts/harvester.py \"{downloaded_path}\" --output \"knowledge_packs/lectures/{downloaded_path.stem}.md\" --vision")
        sys.exit(0)
    else:
        print("[Failure] Could not retrieve textbook.")
        sys.exit(1)

if __name__ == "__main__":
    main()
