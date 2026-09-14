FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
RUN pip install -e .
ENV DATABASE_URL=sqlite:///./data/mm_commerce.db
RUN mkdir -p data
CMD ["python", "-m", "mm_commerce", "run", "--once"]
