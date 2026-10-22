FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY *.py miniapp.html ./
COPY *.json VERSION ./
RUN mkdir -p /app/data
VOLUME /app/data
EXPOSE 8080
CMD ["python", "main.py"]
