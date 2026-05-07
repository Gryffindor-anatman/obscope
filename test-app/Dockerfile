FROM python:3.12-slim

WORKDIR /app

COPY obs /tmp/obs
RUN pip install --no-cache-dir /tmp/obs && rm -rf /tmp/obs

COPY test-app/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY test-app/main.py .

EXPOSE 8001

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8001"]
