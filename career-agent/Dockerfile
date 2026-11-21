FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt && groupadd -r bwg && useradd --no-log-init -r -g bwg bwg
COPY app.py career_parser.py career_pptx.py career_pptx_auto_mapper.py ./
COPY word_ai_core.py word_ai_agent.py word_ai_api.py ./
COPY word_ai_performance.py word_ai_stream.py ./
COPY word_ai_cancel.py ./
COPY career_rag_indexer.py career_rag_auto_sync.py start-agent.sh ./
COPY schema ./schema
COPY career_ai_graph.py career_ai_api.py ./
USER bwg
EXPOSE 8001
HEALTHCHECK --interval=20s --timeout=5s --start-period=15s --retries=5 CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8001/health',timeout=3)" || exit 1
CMD ["sh","/app/start-agent.sh"]
