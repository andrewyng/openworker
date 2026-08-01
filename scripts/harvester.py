import argparse
import os
import json
from pathlib import Path
from pypdf import PdfReader
import aisuite as ai
import re

try:
    import dotenv
    dotenv.load_dotenv(Path(__file__).parent.parent / ".env", override=True)
    if os.environ.get("GEMINI_API_KEY") and "GOOGLE_API_KEY" in os.environ:
        del os.environ["GOOGLE_API_KEY"]
except ImportError:
    pass

SYSTEM_PROMPT = """<role>You are an Expert Pack Harvester.</role>

<instructions>
Read the raw lecture transcripts, slides, or source code and extract Esoteric Knowledge (EK), core concepts, and PRACTICAL EXAMPLES into a valid ExpertPack v4.1 Schema markdown file.

1. EXAMPLES ARE MANDATORY: Include real code examples, architectures, or mathematical formulations.
2. BUG FILTERING: Extract only structurally sound patterns. Document bad code as "Anti-patterns to avoid".
3. EXTERNAL REFERENCES: Generate a "## External References" section containing links to Kaggle, HuggingFace, or official docs.
4. Output MUST start with YAML frontmatter.
5. Provide your strictly formatted markdown output inside <expert_pack> tags. Do not include any conversational preamble.
</instructions>

<format>
---
title: "The Concept Name"
type: "concept"
tags: ["domain:deep-learning", "level:advanced"]
pack: "ai-masters"
---

This is the opening paragraph defining the concept clearly.

## Code Examples / Practical Implementation
```python
def example():
    pass
```

## Anti-Patterns / Mistakes to Avoid
- Don't do X because it causes Y (explain why).

## External References
- **Kaggle**: [Link or search term recommendation]

## Frequently Asked
### Why do we use X instead of Y?
Because...
</format>
"""

def extract_text(file_path: str, use_vision: bool = False, output_path: str = None) -> tuple[str, list[str]]:
    path = Path(file_path)
    ext = path.suffix.lower()
    image_paths = []
    
    if ext == ".pdf":
        if use_vision:
            try:
                import pypdfium2 as pdfium
            except ImportError:
                raise ImportError("Please install pypdfium2 (uv add pypdfium2)")
                
            pdf = pdfium.PdfDocument(file_path)
            if output_path:
                img_dir = Path(output_path).parent / "images"
            else:
                img_dir = Path("knowledge_packs/images")
            img_dir.mkdir(parents=True, exist_ok=True)
            
            for i in range(len(pdf)):
                page = pdf[i]
                image = page.render(scale=2).to_pil()
                img_path = img_dir / f"{path.stem}_page_{i}.png"
                image.save(img_path)
                image_paths.append(str(img_path))
            return "Please process these slide images.", image_paths
        else:
            reader = PdfReader(file_path)
            text = []
            for page in reader.pages:
                extracted = page.extract_text()
                if extracted:
                    text.append(extracted)
            return "\n".join(text), []
        
    elif ext == ".ipynb":
        with open(file_path, 'r', encoding='utf-8') as f:
            notebook = json.load(f)
        text = []
        for cell in notebook.get('cells', []):
            if cell.get('cell_type') in ['markdown', 'code']:
                source = "".join(cell.get('source', []))
                if cell['cell_type'] == 'code':
                    text.append(f"```python\n{source}\n```")
                else:
                    text.append(source)
        return "\n\n".join(text), []
        
    elif ext in [".py", ".txt", ".md", ".csv", ".json"]:
        with open(file_path, 'r', encoding='utf-8') as f:
            return f.read(), []
            
    else:
        raise ValueError(f"Unsupported file extension: {ext}")

import base64

def generate_expert_pack(text: str, image_paths: list[str] = None, use_local: bool = False, model_name: str = None) -> str:
    import os
    try:
        if os.getenv("USE_PROXY") == "1":
            from openai import OpenAI
            client = OpenAI(
                base_url="http://127.0.0.1:8080/v1",
                api_key="proxy"
            )
            model = model_name or "gemini-2.5-flash"
        elif use_local:
            from openai import OpenAI
            client = OpenAI(
                base_url="http://127.0.0.1:8000/v1",
                api_key=os.getenv("LOCAL_LLM_API_KEY", "local-llm")
            )
            model = model_name or "Qwen3.6-35B-A3B-6bit"
        else:
            client = ai.Client()
            if model_name and ":" in model_name:
                model = model_name
            elif model_name:
                if model_name.startswith("gpt"):
                    model = f"openai:{model_name}"
                elif model_name.startswith("claude"):
                    model = f"anthropic:{model_name}"
                elif model_name.startswith("gemini"):
                    model = f"gemini:{model_name}"
                else:
                    model = f"openai:{model_name}"
            elif os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY"):
                model = "gemini:gemini-2.5-flash"
            elif os.getenv("OPENAI_API_KEY"):
                model = "openai:gpt-4o"
            elif os.getenv("ANTHROPIC_API_KEY"):
                model = "anthropic:claude-3-5-sonnet-20240620"
            else:
                raise ValueError("No API keys found for cloud models (GEMINI_API_KEY, ANTHROPIC_API_KEY or OPENAI_API_KEY). Use --local.")
        
        content = [{"type": "text", "text": f"<input>\n{text}\n</input>"}]
        
        if image_paths:
            for img_path in image_paths:
                with open(img_path, "rb") as img_file:
                    b64_image = base64.b64encode(img_file.read()).decode('utf-8')
                content.append({
                    "type": "image_url",
                    "image_url": {"url": f"data:image/png;base64,{b64_image}"}
                })
        
        print(f"Calling model: {model} ...")
        import time
        max_retries = 5
        retry_delay = 30
        for attempt in range(max_retries):
            try:
                response = client.chat.completions.create(
                    model=model,
                    messages=[
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": content}
                    ],
                    temperature=0.2,
                )
                break
            except Exception as e:
                err_str = str(e).lower()
                is_rate_limit = False
                if "429" in err_str or "quota" in err_str or "exhausted" in err_str or "rate" in err_str or "retry" in err_str or "resource" in err_str:
                    is_rate_limit = True
                elif hasattr(e, "code") and e.code == 429:
                    is_rate_limit = True
                elif hasattr(e, "status_code") and e.status_code == 429:
                    is_rate_limit = True
                
                if is_rate_limit:
                    if attempt < max_retries - 1:
                        print(f"[Warning] Rate limit hit. Retrying in {retry_delay}s... (Attempt {attempt+1}/{max_retries})")
                        time.sleep(retry_delay)
                        continue
                raise e
        
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
            
        return out.strip()
    except Exception as e:
        raise e

def main():
    parser = argparse.ArgumentParser(description="Harvest AI Lectures and Code into Expert Packs")
    parser.add_argument("file_path", help="Path to the lecture PDF, Notebook, or Python script")
    parser.add_argument("--output", help="Path to save the output markdown file", required=True)
    parser.add_argument("--vision", action="store_true", help="Use Multimodal Vision for PDFs")
    parser.add_argument("--auto", action="store_true", help="Skip review and auto-save")
    parser.add_argument("--local", action="store_true", help="Use local LLM on port 8000")
    parser.add_argument("--model", type=str, default=None, help="Model string (e.g. openai:gpt-4o, or Qwen3.6-35B-A3B-6bit)")
    args = parser.parse_args()
    
    print(f"Reading {args.file_path}...")
    import sys
    try:
        raw_text, image_paths = extract_text(args.file_path, use_vision=args.vision, output_path=args.output)
    except Exception as e:
        print(f"Error reading file: {e}")
        sys.exit(1)
        
    print(f"Extracted content. Generating Expert Pack...")
    try:
        markdown_content = generate_expert_pack(
            raw_text, 
            image_paths if args.vision else None,
            use_local=args.local,
            model_name=args.model
        )
    except Exception as e:
        print(f"Error generating content: {e}")
        sys.exit(1)
        
    out_path = Path(args.output)
    
    if args.auto:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(markdown_content)
        print(f"Successfully created Expert Pack at {out_path}")
    else:
        # Interactive Review
        draft_path = Path("drafts") / out_path.name
        draft_path.parent.mkdir(parents=True, exist_ok=True)
        draft_path.write_text(markdown_content)
        
        print("\n" + "="*50)
        print("DRAFT GENERATED!")
        print(f"Preview saved to: {draft_path}")
        print("="*50 + "\n")
        print(markdown_content[:500] + "\n...\n")
        
        resp = input(f"Review the draft above. Save to {out_path}? (y/n): ")
        if resp.lower().startswith('y'):
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(markdown_content)
            draft_path.unlink() # cleanup
            print(f"Successfully saved Expert Pack to {out_path}")
        else:
            print("Aborted. Draft remains in the drafts folder.")

if __name__ == "__main__":
    main()
