FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8050

# Bind to 0.0.0.0 so the port is reachable from outside the container
CMD ["python", "-c", \
     "from app import app; app.run(host='0.0.0.0', port=8050, debug=False)"]
