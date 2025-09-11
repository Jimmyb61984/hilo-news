FROM python:3.12-slim

# helpful defaults (optional)
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

# install deps first (better cache)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# copy your code (copy everything so data/schemas are available if needed)
COPY . .

# Render provides PORT at runtime; give a default for local runs too
ENV PORT=10000

# IMPORTANT: bind to 0.0.0.0:$PORT (not 8080)
# use sh -c so $PORT is expanded
CMD ["sh","-c","uvicorn app.main:app --host 0.0.0.0 --port $PORT"]

