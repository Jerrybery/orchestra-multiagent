FROM python:3.11-slim

WORKDIR /app

COPY pyproject.toml .
COPY src/ src/
COPY prompts/ prompts/

RUN pip install --no-cache-dir ".[server]"

EXPOSE 8420

CMD ["orchestra", "web", "--host", "0.0.0.0", "--port", "8420"]
