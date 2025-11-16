from fastapi import FastAPI
from app.security.headers import security_headers
from app.security.cors import add_cors
from app.security.logging import setup_logging, RequestIDMiddleware
from app.routes_auth import router as auth_router
from app.routes_bac import router as bac_router
from app.routes_apps import private as apps_router
from app.security.settings import APP_ENV, SECRET_KEY,STRICT_SECRETS
from app.security.crypto import enforce_secret_strength
from app.db.bootstrap import init_db
from app.security.limits import BodySizeLimit
from app.security.settings import BODY_MAX_BYTES
from app.router_dbg import router as debug_router
from app.security.integrety import enforce_integrity_from_env
from app.security.observability import metrics_middleware, metrics_endpoint
from app.routes_ssrf_demo import router as ssrf_router
from fastapi import FastAPI, UploadFile, File, HTTPException
from utils.cv_processing_utils import split_cv_into_sections, process_cv, combine_results
from utils.file_extractor import extract_text_from_pdf, extract_text_from_docx
from models.t5_model import T5Model
import json
import json5
import subprocess
from typing import Any, Dict
import requests

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from models.schemas import UserProfile, CareerInsightsResponse
from prompts.career_prompt import SYSTEM_PROMPT, build_user_prompt


setup_logging()
enforce_secret_strength(SECRET_KEY, APP_ENV, strict=STRICT_SECRETS)
init_db()
enforce_integrity_from_env()

app = FastAPI(title="OWASP Top 10 Starter")
app.add_middleware(BodySizeLimit, max_body_bytes=BODY_MAX_BYTES)

# Middlewares
app.middleware("http")(security_headers)
app.add_middleware(RequestIDMiddleware)
add_cors(app)

# ✅ Register routers 
app.include_router(auth_router)
app.include_router(bac_router)
app.include_router(debug_router)

app.include_router(apps_router)

app.include_router(ssrf_router)

@app.get("/health")
def health():
    return {"ok": True, "service": "api", "status": "healthy"}

app.middleware("http")(metrics_middleware)

@app.get("/metrics")
def _metrics():
    return metrics_endpoint()
app = FastAPI()

# Initialize the T5 model
t5_model = T5Model()

@app.post("/upload/")
async def upload_cv(file: UploadFile = File(...)):
    if not file.filename.endswith(('.pdf', '.docx')):
        raise HTTPException(status_code=400, detail="Unsupported file format. Please upload a PDF or DOCX file.")

    # Extract text from the file
    if file.filename.endswith('.pdf'):
        text = extract_text_from_pdf(file.file)
    elif file.filename.endswith('.docx'):
        text = extract_text_from_docx(file.file)

    # Process the CV in chunks
    structured_data = t5_model.process_cv(text)

    # Return the structured data
    return {"structured_data": structured_data}
@app.post("/improve/")
async def improve_cv_endpoint(cv_data: dict):
    """
    Improve the CV by enhancing content and generating suggestions.
    :param cv_data: A dictionary containing structured CV data.
    :return: A dictionary with improved CV content and suggestions.
    """
    # Use the T5 model to improve the CV
    improved_cv = t5_model.improve_cv(cv_data)

    # Return the improved CV
    return {"improved_cv": improved_cv}


app = FastAPI(
    title="MENA Career Insight API",
    description="Local career insights powered by Ollama + LLM",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def call_ollama(model: str, system_prompt: str, user_prompt: str) -> str:
    """
    Call the local Ollama HTTP API and return the full response text.
    """
    url = "http://127.0.0.1:11434/api/generate"

    prompt = f"<<SYS>>\n{system_prompt}\n<</SYS>>\n\n{user_prompt}"

    payload = {
        "model": model,
        "prompt": prompt,
        "stream": False  # single JSON response
    }

    try:
        resp = requests.post(url, json=payload)
        resp.raise_for_status()
    except Exception as e:
        raise RuntimeError(f"Ollama HTTP error: {e}")

    data = resp.json()
    text = data.get("response", "") or ""
    return text.strip()

def extract_json(text: str) -> str:
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError("No JSON object found in text")
    return text[start : end + 1]


@app.post("/career-insights", response_model=CareerInsightsResponse)
def generate_career_insights(profile: UserProfile):
    try:
        user_prompt = build_user_prompt(profile)

        raw_output = call_ollama(
            model="llama3.2:3b",
            system_prompt=SYSTEM_PROMPT,
            user_prompt=user_prompt,
        )

        print("RAW OUTPUT FROM MODEL:")
        print(raw_output)

        # First, extract JSON part
        json_text = extract_json(raw_output)

        # Try strict json first
        try:
            parsed: Dict[str, Any] = json.loads(json_text)
        except json.JSONDecodeError:
            # Fallback: use json5 to tolerate minor issues like missing commas
            parsed = json5.loads(json_text)

        return CareerInsightsResponse(**parsed)

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/health")
def health():
    return {"status": "ok"}