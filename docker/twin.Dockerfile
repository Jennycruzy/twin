# The Twin tools image: dbt, DataHub ingestion, the DataHub MCP server, and Twin itself.
#
# Python 3.11 rather than 3.10: mcp-server-datahub requires >=3.11.
FROM python:3.11-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# git: dbt inspects the working tree. postgresql-client: warehouse checks in the Makefile.
# build-essential + libpq-dev: psycopg2 has no wheel for every platform we may build on.
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      git \
      curl \
      postgresql-client \
      build-essential \
      libpq-dev \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt /app/requirements.txt
RUN pip install -r /app/requirements.txt \
 && apt-get purge -y --auto-remove build-essential \
 && rm -rf /var/lib/apt/lists/*

# The working tree is bind-mounted over /app at run time; this copy only exists so the
# image is usable standalone (CI, `docker run twin:local`).
COPY . /app

ENTRYPOINT []
CMD ["python", "-c", "import twin; print(twin.__doc__)"]
