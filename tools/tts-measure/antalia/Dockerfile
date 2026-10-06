# pagentos-antalia-measure - Antalia 1 MEASUREMENT image (tts-antalia-measure), beside FreyaTTS.
# Pins: team/plans/tts-antalia-integration-plan.md sections 2-3 (same base digest as FreyaTTS).
# Never the API image; no torch there.
FROM python:3.12-slim-bookworm@sha256:54c85f3c47607a77f32adec749d3c81d1348bf25833671f512b26a9b6d778cb3
ENV PIP_DISABLE_PIP_VERSION_CHECK=1 PYTHONDONTWRITEBYTECODE=1
COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir --no-deps --require-hashes --extra-index-url https://download.pytorch.org/whl/cpu -r /tmp/requirements.txt && rm /tmp/requirements.txt
# The Antalia code and the BigVGAN code at their pinned commits, every file sha256-checked, the
# one-hunk BigVGAN patch applied and re-checked (a mismatch fails the build).
COPY fetch_code.py /tmp/fetch_code.py
RUN python /tmp/fetch_code.py /opt && rm /tmp/fetch_code.py
COPY synthesize.py /opt/tts-measure/synthesize.py
# MPLCONFIGDIR / NUMBA_CACHE_DIR: matplotlib and numba (imported by BigVGAN's utils/meldataset)
# write under $HOME on import; the image runs --read-only, /tmp is the run's tmpfs.
ENV PYTHONPATH=/opt/antalia:/opt/bigvgan HF_HUB_OFFLINE=1 HF_HUB_DISABLE_TELEMETRY=1 HF_HOME=/tmp/hf MPLCONFIGDIR=/tmp/mpl NUMBA_CACHE_DIR=/tmp/numba OMP_NUM_THREADS=4
RUN useradd --uid 10001 --no-create-home antalia && mkdir /models /out && chown 10001 /models /out
USER antalia
WORKDIR /opt/antalia
RUN python /opt/tts-measure/synthesize.py selfcheck
ENTRYPOINT ["python", "/opt/tts-measure/synthesize.py"]
