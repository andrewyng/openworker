import os
import time
import logging
from pathlib import Path
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse
import uvicorn
import httpx
import dotenv

# Load environment variables
dotenv.load_dotenv(Path(__file__).parent.parent / ".env", override=True)
if os.environ.get("GEMINI_API_KEY") and "GOOGLE_API_KEY" in os.environ:
    del os.environ["GOOGLE_API_KEY"]

# Setup Logging
LOG_FILE = Path(__file__).parent.parent / "proxy_trace.log"
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger("api_proxy")

logger.info("==================================================")
logger.info("Proxy Server Starting...")
logger.info(f"Writing traces to {LOG_FILE}")
logger.info("==================================================")

app = FastAPI(title="Cloud API Failover Proxy Server")

# Initialize HTTP clients
local_client = httpx.AsyncClient(base_url="http://127.0.0.1:8000/v1", timeout=120.0)

import aisuite as ai
ai_client = ai.Client()

def is_rate_limit_error(e: Exception) -> bool:
    err_str = str(e).lower()
    if "429" in err_str or "quota" in err_str or "exhausted" in err_str or "rate" in err_str or "retry" in err_str or "resource" in err_str:
        return True
    if hasattr(e, "code") and e.code == 429:
        return True
    if hasattr(e, "status_code") and e.status_code == 429:
        return True
    return False

@app.post("/v1/chat/completions")
async def chat_completions(request: Request):
    body = await request.json()
    model = body.get("model", "gemini-2.5-flash")
    messages = body.get("messages", [])
    temperature = body.get("temperature", 0.2)
    
    logger.info(f"Received request for model: {model}")
    
    # Resolve the cloud provider model string for aisuite
    aisuite_model = model
    if ":" not in aisuite_model:
        if aisuite_model.startswith("gpt"):
            aisuite_model = f"openai:{aisuite_model}"
        elif aisuite_model.startswith("claude"):
            aisuite_model = f"anthropic:{aisuite_model}"
        elif aisuite_model.startswith("gemini"):
            aisuite_model = f"gemini:{aisuite_model}"
        else:
            if os.getenv("GEMINI_API_KEY"):
                aisuite_model = f"gemini:{aisuite_model}"
            else:
                aisuite_model = f"openai:{aisuite_model}"
                
    start_time = time.time()
    
    # Try cloud first
    try:
        import asyncio
        loop = asyncio.get_event_loop()
        
        models_to_try = []
        if "gemini" in aisuite_model:
            # Prioritize gemini-3.5-flash as it holds the only active daily free-tier quota
            models_to_try = ["gemini:gemini-3.5-flash"]
        else:
            models_to_try = [aisuite_model]
                    
        response = None
        last_exception = None
        successful_model = None
        
        for current_model in models_to_try:
            # Increase retry attempts and wait time for cloud models to handle per-minute quotas
            for attempt in range(5):
                try:
                    logger.info(f"Attempting cloud generation with model {current_model} (Attempt {attempt+1}/5)...")
                    def call_aisuite():
                        return ai_client.chat.completions.create(
                            model=current_model,
                            messages=messages,
                            temperature=temperature
                        )
                    response = await loop.run_in_executor(None, call_aisuite)
                    successful_model = current_model
                    break
                except Exception as e:
                    err_str = str(e).lower()
                    logger.warning(f"Attempt {attempt+1}/5 failed for model {current_model}: {e}")
                    last_exception = e
                    if attempt < 4 and ("429" in err_str or "503" in err_str or "unavailable" in err_str or "overloaded" in err_str or "quota" in err_str or "limit" in err_str):
                        logger.info("Rate limit hit or model overloaded. Sleeping for 15s before retry...")
                        await asyncio.sleep(15.0)
                        continue
                    break
            if response is not None:
                break
            await asyncio.sleep(1.0)
                
        if response is None:
            if last_exception:
                raise last_exception
            else:
                raise ValueError("All cloud models failed to generate response.")
                
        latency = time.time() - start_time
        logger.info(f"Cloud request succeeded in {latency:.2f}s using {successful_model}")
        
        choice = response.choices[0]
        return {
            "id": getattr(response, "id", f"chatcmpl-{int(time.time())}"),
            "object": "chat.completion",
            "created": getattr(response, "created", int(time.time())),
            "model": model,
            "choices": [
                {
                    "index": 0,
                    "message": {
                        "role": choice.message.role,
                        "content": choice.message.content
                    },
                    "finish_reason": getattr(choice, "finish_reason", "stop")
                }
            ],
            "usage": {
                "prompt_tokens": getattr(response.usage, "prompt_tokens", 0) if hasattr(response, "usage") else 0,
                "completion_tokens": getattr(response.usage, "completion_tokens", 0) if hasattr(response, "usage") else 0,
                "total_tokens": getattr(response.usage, "total_tokens", 0) if hasattr(response, "usage") else 0
            }
        }
        
    except Exception as e:
        latency = time.time() - start_time
        logger.warning(f"Cloud generation failed in {latency:.2f}s: {e}")
        
        logger.error(f"Cloud generation failed completely: {e}")
        raise HTTPException(status_code=502, detail=str(e))

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8080)
