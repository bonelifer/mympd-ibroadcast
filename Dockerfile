FROM python:3.13-slim
WORKDIR /app
COPY bridge.py .
ENTRYPOINT ["python3", "/app/bridge.py"]
