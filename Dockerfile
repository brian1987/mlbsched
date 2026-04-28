FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY mlbsched.py server.py db.py odds.py bestbets.py weather.py streaks.py leaders.py wildcard.py h2h.py player.py lineup.py ./
EXPOSE 8080
CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8080"]
