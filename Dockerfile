FROM python:3.11-slim 
WORKDIR /app 
COPY requirements.txt . 
RUN pip install --no-cache-dir -r requirements.txt 
COPY DMRC-GTFS/ ./DMRC-GTFS/ 
COPY . . 
EXPOSE 8080
HEALTHCHECK CMD curl --fail http://localhost:8501/_stcore/health || exit 1 
ENTRYPOINT ["streamlit", "run", "ui/streamlit_app.py", "--server.port=8080", "--server.address=0.0.0.0", "--server.headless=true"]
