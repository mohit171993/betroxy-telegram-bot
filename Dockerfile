FROM mcr.microsoft.com/playwright/python:v1.55.0-noble

WORKDIR /app

COPY checker_requirements.txt /app/checker_requirements.txt
RUN pip install --no-cache-dir -r /app/checker_requirements.txt

COPY cloud_checker_worker.py /app/cloud_checker_worker.py
COPY cloud_checker_app.py /app/cloud_checker_app.py
COPY cloud_checker_live.py /app/cloud_checker_live.py

ENV PYTHONUNBUFFERED=1

CMD ["python", "/app/cloud_checker_live.py"]
