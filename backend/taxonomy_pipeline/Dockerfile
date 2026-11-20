# Taxonomy pipeline API / CLI. Built by the repository's compose.yaml, which mounts backend/ at
# /app/backend so the editable install below runs the host's source and reads and writes the host's data.
FROM python:3.12-slim

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HF_HOME=/opt/huggingface

WORKDIR /app/backend/taxonomy_pipeline

# CPU-only PyTorch: the default Linux wheel bundles CUDA and is several GB larger.
RUN pip install torch --index-url https://download.pytorch.org/whl/cpu

# Dependencies first, against a placeholder package, so source edits don't reinstall them.
COPY pyproject.toml ./
RUN mkdir -p src/taxonomy_pipeline && touch src/taxonomy_pipeline/__init__.py \
    && pip install -e ".[dev,semantic,llm]"

COPY src ./src
RUN pip install --no-deps -e . \
    # Download the pinned sentence model into the image so semantic steps run offline.
    && python -c "from taxonomy_pipeline.matching.embeddings import _get_model; _get_model()"

ENV HF_HUB_OFFLINE=1
EXPOSE 8000
CMD ["taxonomy-pipeline", "serve", "--host", "0.0.0.0", "--port", "8000"]
