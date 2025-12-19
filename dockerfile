FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
  ffmpeg \
  libopus0 \
  libsodium23 \
  build-essential \
  gcc \
  python3-dev \
  libffi-dev \
  libsodium-dev \
  && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /app/requirements.txt
RUN python -m pip install --upgrade pip setuptools wheel
RUN pip install --no-cache-dir -U yt-dlp
RUN pip install --no-cache-dir -r requirements.txt

COPY . /app

CMD ["python", "index.py"]
