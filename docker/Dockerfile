FROM python:3.11-slim
WORKDIR /app
COPY pyproject.toml .
RUN pip install .
COPY agents/ agents/
COPY servers/ servers/
RUN mkdir -p notes
RUN useradd -m appuser && chown -R appuser /app
USER appuser
EXPOSE 8000
CMD ["uvicorn", "agents.main:app", "--host", "0.0.0.0", "--port", "8000"]
