install:
	pip install -e ".[dev]"

run-api:
	uvicorn agents.main:app --reload --port 8000

run-ui:
	streamlit run frontend/app.py --server.port 8501

test:
	pytest tests/ -v

docker-up:
	cd docker && docker compose up -d

docker-down:
	cd docker && docker compose down

docker-logs:
	cd docker && docker compose logs -f
