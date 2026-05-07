FROM python:3.12-slim

WORKDIR /app

# Install the shared obs package first (its deps cover all OTel libs).
COPY obs /tmp/obs
RUN pip install --no-cache-dir /tmp/obs && rm -rf /tmp/obs

COPY app/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/main.py .

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
