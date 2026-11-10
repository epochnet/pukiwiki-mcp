# PukiWiki MCP server image
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Install dependencies first (better layer caching).
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir .

# Default transport for the container is HTTP so it works with `compose up`.
# Override with PUKIWIKI_MCP_TRANSPORT=stdio for `docker run -i` (stdio) use.
# 0.0.0.0 is needed inside the container; publish the port on the host's
# 127.0.0.1 only (e.g. `-p 127.0.0.1:8000:8000`) -- there is no authentication.
ENV PUKIWIKI_MCP_TRANSPORT=streamable-http \
    PUKIWIKI_MCP_HOST=0.0.0.0 \
    PUKIWIKI_MCP_PORT=8000

EXPOSE 8000

ENTRYPOINT ["python", "-m", "pukiwiki_mcp"]
