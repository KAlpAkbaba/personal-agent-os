# Radicale - the owner's own CalDAV server (radicale-stack-ops; plan
# team/plans/radicale-stack-ops-integration.md). Radicale is GPL-3.0 and runs here unmodified,
# as its own process; the api talks to it over HTTP only. The image is built on the host and
# never distributed.
#
# Reproducible: the base is pinned by digest and the whole closure is installed `==` with
# --no-deps, then `pip check` fails the build if anything is missing. Bump the pins together.
FROM python:3.12-alpine@sha256:1b668429b3511ab407d8e00648891631b0b1a4d7e15e3ca70f38ab5b91ad4ab4

# The uid that owns /data. install-radicale.sh and restore-cloud-core.sh chown to the same
# number (test_radicale_stack.py reads all three).
ARG RADICALE_UID=10002

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1

RUN pip install --no-deps \
        radicale==3.8.1 bcrypt==5.0.0 libpass==1.9.3 defusedxml==0.7.1 vobject==0.9.9 \
        python-dateutil==2.9.0.post0 six==1.17.0 pytz==2026.5 pika==1.4.4 requests==2.34.2 \
        urllib3==2.8.0 idna==3.20 charset-normalizer==3.5.2 certifi==2026.7.22 \
 && pip check \
 && adduser -D -H -u ${RADICALE_UID} radicale \
 && mkdir -p /etc/radicale /data \
 && chown radicale:radicale /data

COPY config /usr/local/etc/radicale/config
COPY entrypoint.sh /usr/local/bin/pagentos-radicale-entrypoint
RUN chmod 0644 /usr/local/etc/radicale/config && chmod 0755 /usr/local/bin/pagentos-radicale-entrypoint

USER radicale
EXPOSE 5232
ENTRYPOINT ["/usr/local/bin/pagentos-radicale-entrypoint"]
