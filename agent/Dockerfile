# AgentCore Runtime requires an ARM64 (linux/arm64) image. CDK builds it with
# platform=LINUX_ARM64; for a manual build use: docker buildx build --platform linux/arm64 .
# Layout follows the official AgentCore CLI Python container template.
FROM public.ecr.aws/docker/library/python:3.12-slim-trixie

RUN apt-get update && apt-get upgrade -y && rm -rf /var/lib/apt/lists/*

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    DOCKER_CONTAINER=1 \
    WORK_ROOT=/tmp/work \
    CLAUDE_CONFIG_DIR=/tmp/claude-config \
    HOME=/tmp

WORKDIR /app
RUN useradd -m -u 1000 bedrock_agentcore

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY --chown=bedrock_agentcore:bedrock_agentcore main.py analyzer.py repo_fetch.py schema.py ./

USER bedrock_agentcore

# AgentCore Runtime service contract: HTTP protocol on port 8080 (/invocations, /ping).
EXPOSE 8080

CMD ["python", "main.py"]
