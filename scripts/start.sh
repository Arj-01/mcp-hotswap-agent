#!/bin/bash
echo "Starting MCP Assistant..."
cd docker && docker compose up -d
echo "Pulling Ollama model (first time only)..."
docker compose exec ollama ollama pull llama3.2
echo ""
echo "Ready!"
echo "  UI:         http://localhost:8501"
echo "  API Docs:   http://localhost:8000/docs"
echo "  Grafana:    http://localhost:3000"
echo "  Prometheus: http://localhost:9090"
echo
