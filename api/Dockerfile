# 3.13 to match the environment the tests run in. numpy 2.5 no longer ships
# wheels for 3.11, so the pinned lock would not even install on the old base.
FROM python:3.13-slim

# RDKit's wheel links against these two X libs even for headless use, and its
# drawing module (depict.py, the structure SVGs) needs libexpat as well: without
# it `import main` fails at startup, which only a Linux build shows.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libxrender1 libxext6 libexpat1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
# The lock, not requirements.txt: the box rebuilds this image on every deploy,
# and only a full lock guarantees it installs what the tests ran against.
COPY requirements.lock .
RUN pip install --no-cache-dir -r requirements.lock
COPY . .

EXPOSE 8000
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
