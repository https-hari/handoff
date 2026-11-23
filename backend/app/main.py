"""Handoff API.

POST /handoff  conversation -> state card + ready-to-paste prompt
POST /verify   card + new model's reply -> which rules were kept
GET  /stats    compression and retention across all handoffs
"""
import os
from typing import Optional

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from .db import Store
from .extractor import extract_state
from .llm import LLM, FallbackLLM, LLMError, estimate_tokens, providers_from_env
from .render import render_prompt
from .schemas import (HandoffRequest, HandoffResponse, HandoffStats,
                      VerifyRequest, VerifyResponse)
from .verify import verify_reply

load_dotenv()

app = FastAPI(title="Handoff", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    # only the browser extension and local tools call this API
    allow_origin_regex=r"^(chrome-extension://.*|http://localhost(:\d+)?)$",
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)

_store = Store(os.getenv("HANDOFF_DB", "data/handoff.db"))
_llm: Optional[LLM] = None


def get_llm() -> LLM:
    global _llm
    if _llm is None:
        try:
            _llm = FallbackLLM(providers_from_env())
        except LLMError as e:
            raise HTTPException(503, str(e))
    return _llm


def get_store() -> Store:
    return _store


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/handoff", response_model=HandoffResponse)
def handoff(req: HandoffRequest, llm: LLM = Depends(get_llm), store: Store = Depends(get_store)):
    window = int(os.getenv("WINDOW_TOKENS", "3000"))
    try:
        card, windows = extract_state(llm, req.conversation, window_tokens=window)
    except LLMError as e:
        raise HTTPException(502, str(e))
    except ValueError as e:
        raise HTTPException(422, str(e))

    prompt = render_prompt(card, req.target, req.workspace)
    source_tokens = estimate_tokens("\n".join(m.content for m in req.conversation.messages))
    prompt_tokens = estimate_tokens(prompt)
    hid = store.add_handoff(req.conversation.source, req.target, source_tokens,
                            prompt_tokens, windows, card.model_dump_json())
    return HandoffResponse(
        handoff_id=hid, card=card, prompt=prompt,
        stats=HandoffStats(source_tokens=source_tokens, prompt_tokens=prompt_tokens,
                           compression=round(prompt_tokens / source_tokens, 3), windows=windows))


@app.post("/verify", response_model=VerifyResponse)
def verify(req: VerifyRequest, llm: LLM = Depends(get_llm), store: Store = Depends(get_store)):
    try:
        result = verify_reply(llm, req.card, req.reply)
    except LLMError as e:
        raise HTTPException(502, str(e))
    except ValueError as e:
        raise HTTPException(422, str(e))
    if req.handoff_id is not None:
        kept = len(result.checks) - len(result.missing)
        store.add_verification(req.handoff_id, result.goal_correct, kept, len(result.checks))
    return result


@app.get("/stats")
def stats(store: Store = Depends(get_store)):
    return store.stats()
