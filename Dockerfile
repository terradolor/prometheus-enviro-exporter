FROM ubuntu:20.04

RUN apt-get update && apt-get install -y \
    python3 \
    python3-pip

COPY requirements.txt .

RUN pip3 install -r requirements.txt

COPY prometheus-enviro-exporter.py .

CMD python -B prometheus-enviro-exporter.py --bind=0.0.0.0
