# ==============================================================================
# SS Retro Bot — Production 24/7 Cloud Dockerfile
# Optimized for Render.com, Koyeb, Railway, VPS, and Docker-based Cloud Platforms
# ==============================================================================

FROM python:3.11-slim

# Prevent writing .pyc files and enable unbuffered output for real-time cloud logs
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8080

# Install system dependencies:
# - ffmpeg: Required for 320kbps MP3 conversion, audio tagging, and Audio FX Studio
# - ca-certificates: Required for secure SSL/TLS connections
# - curl: For platform container health checks
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    ca-certificates \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Set working directory inside container
WORKDIR /app

# Copy dependency definition first to leverage Docker layer caching
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Copy application source code
COPY . .

# Ensure storage directories exist
RUN mkdir -p data/temp_downloads

# Expose cloud health check port (Render/Koyeb/Railway bind to $PORT)
EXPOSE 8080

# Start SS Retro Bot
CMD ["python", "bot.py"]
