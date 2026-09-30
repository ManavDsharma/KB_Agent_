# Container image for Amazon Bedrock AgentCore Runtime (linux/arm64, port 8080).
# Build from this directory:  docker build --platform linux/arm64 -t dtp-operate-agent .
#
# The agent modules import each other by plain name, so the app must run from
# its own directory (WORKDIR /app below).

FROM --platform=linux/arm64 python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    ENVIRONMENT=production

WORKDIR /app

# Install the dependencies declared in pyproject.toml (single source of truth).
COPY pyproject.toml .
RUN python -c "import tomllib; print('\n'.join(tomllib.load(open('pyproject.toml','rb'))['project']['dependencies']))" > /tmp/requirements.txt \
    && pip install -r /tmp/requirements.txt \
    && rm /tmp/requirements.txt

COPY . .

RUN useradd --create-home --uid 1000 agent && chown -R agent:agent /app
USER agent

EXPOSE 8080

# opentelemetry-instrument gives AgentCore observability (traces, metrics).
CMD ["opentelemetry-instrument", "python", "main.py"]
