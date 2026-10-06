# pagentos-speaker-measure - speaker embedding + diarisation MEASUREMENT image (speaker-engine-measure).
# Pins: team/plans/speaker-engine-integration-plan.md sections 2-3. Never the API image; the API
# never imports sherpa_onnx. Same pinned base as FreyaTTS (tools/tts-measure/Dockerfile).
FROM python:3.12-slim-bookworm@sha256:54c85f3c47607a77f32adec749d3c81d1348bf25833671f512b26a9b6d778cb3
ENV PIP_DISABLE_PIP_VERSION_CHECK=1 PYTHONDONTWRITEBYTECODE=1 OMP_NUM_THREADS=4
COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir --no-deps --require-hashes --only-binary=:all: -r /tmp/requirements.txt && rm /tmp/requirements.txt
COPY measure.py fetch_models.py /opt/speaker-measure/
RUN useradd --uid 10001 --no-create-home spk && mkdir /models /out && chown 10001 /models /out
USER 10001
WORKDIR /opt/speaker-measure
RUN python /opt/speaker-measure/measure.py selfcheck
ENTRYPOINT ["python", "/opt/speaker-measure/measure.py"]
