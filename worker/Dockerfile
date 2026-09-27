# The OpenMM simulation worker. Separate from api/ on purpose: OpenMM needs
# conda, and this image is several GB that the API has no reason to carry.
FROM mambaorg/micromamba:2.0.5-ubuntu22.04

# The lock, not environment.yml: exact packages, installed without a solve.
COPY --chown=$MAMBA_USER:$MAMBA_USER conda-linux-64.lock /tmp/conda-linux-64.lock
# One extraction thread: unpacking the CUDA libraries in parallel is what runs a
# 4 GB Docker Desktop VM out of memory.
ENV MAMBA_EXTRACT_THREADS=1
RUN micromamba install -y -n base -f /tmp/conda-linux-64.lock \
    && micromamba clean --all --yes

WORKDIR /app
COPY --chown=$MAMBA_USER:$MAMBA_USER *.py ./

# Unbuffered so progress shows in `docker compose logs worker` as it happens.
ENV PYTHONUNBUFFERED=1
CMD ["python", "worker.py"]
