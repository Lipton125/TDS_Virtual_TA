# Use official Python runtime as a parent image
FROM python:3.11-slim

# Install system dependencies (including tesseract-ocr)
RUN apt-get update && apt-get install -y tesseract-ocr libtesseract-dev && rm -rf /var/lib/apt/lists/*

# Set working directory in container
WORKDIR /app

# Copy your application code to the container
COPY . /app

# Install python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Expose port 8000
EXPOSE 8000

# Command to run the FastAPI app using uvicorn
CMD ["uvicorn", "virtual_ta_api:app", "--host", "0.0.0.0", "--port", "8000"]

