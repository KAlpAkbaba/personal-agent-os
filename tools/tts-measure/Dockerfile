# pagentos-freya-measure - FreyaTTS-small MEASUREMENT image (tts-freya-measure).
# Pins: team/plans/tts-freya-integration-plan.md sections 2-3. Never the API image; no torch there.
FROM python:3.12-slim-bookworm@sha256:54c85f3c47607a77f32adec749d3c81d1348bf25833671f512b26a9b6d778cb3
ENV PIP_DISABLE_PIP_VERSION_CHECK=1 PYTHONDONTWRITEBYTECODE=1
COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir --no-deps --require-hashes --extra-index-url https://download.pytorch.org/whl/cpu -r /tmp/requirements.txt && rm /tmp/requirements.txt
# The code at the pinned commit, every file sha256-checked (a mismatch fails the build).
COPY fetch_code.py /tmp/fetch_code.py
RUN python /tmp/fetch_code.py /opt/freyatts && rm /tmp/fetch_code.py
COPY synthesize.py /opt/tts-measure/synthesize.py
# NUMBA_CACHE_DIR: the image runs --read-only as a non-root user; without a writable numba cache
# librosa.pyin raises and upstream's _voiced_ok swallows it (returns True): the collapse guard
# would be silently off. /tmp is the run's tmpfs.
ENV PYTHONPATH=/opt/freyatts HF_HUB_OFFLINE=1 HF_HUB_DISABLE_TELEMETRY=1 HF_HOME=/tmp/hf OMP_NUM_THREADS=4 NUMBA_CACHE_DIR=/tmp/numba
RUN useradd --uid 10001 --no-create-home freya && mkdir /models /out && chown 10001 /models /out
USER freya
WORKDIR /opt/freyatts
RUN python /opt/tts-measure/synthesize.py selfcheck
ENTRYPOINT ["python", "/opt/tts-measure/synthesize.py"]
