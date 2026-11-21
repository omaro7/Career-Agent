import os
from datetime import datetime, timezone
import httpx
from fastapi import FastAPI
from .db import connect
from .profiles import router as profile_router
from .profile_delete import router as profile_delete_router
from .pptx import router as pptx_router
from .template_registry import router as template_registry_router
from .word_imports import router as word_import_router
from .word_project_routes import router as word_project_router
from .word_ai_runs import router as word_ai_router

app=FastAPI(title='BWG Career API (Stage 02-4)',version='0.1.0')
app.include_router(profile_router)
app.include_router(profile_delete_router)
app.include_router(pptx_router)
app.include_router(template_registry_router)
app.include_router(word_import_router)
app.include_router(word_project_router)
app.include_router(word_ai_router)

@app.get('/health')
def health():
    return {'status':'UP','service':'career-api'}

@app.get('/api/v1/system/status')
async def status():
    db='DOWN'; agent='DOWN'
    try:
        with connect() as cn:
            db='UP' if cn.execute('SELECT 1 AS ok').fetchone()['ok']==1 else 'DOWN'
    except Exception: pass
    try:
        async with httpx.AsyncClient(timeout=3) as cli:
            response=await cli.get(os.getenv('AGENT_BASE_URL','http://career-agent:8001').rstrip('/')+'/health')
            agent='UP' if response.status_code==200 else 'DOWN'
    except Exception: pass
    return {'app':'BWG-Career-Agent-v1.0','stage':'02-4','api':'UP','postgresql':db,'careerAgent':agent,'mysql':db,'pythonWorker':agent,'checkedAt':datetime.now(timezone.utc).isoformat()}

# Stage04 local-only Career AI proxy
from .career_ai_proxy import router as career_ai_proxy_router
app.include_router(career_ai_proxy_router)
from .career_ai_apply import router as career_ai_apply_router
app.include_router(career_ai_apply_router)

# Stage 05: Group PPTX (additive)
from .career_group_pptx import router as career_group_pptx_router
app.include_router(career_group_pptx_router)
