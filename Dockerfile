# ASCOM Alpaca FLI server.
#
# libfli talks to FLI cameras through the fliusb kernel char device
# (/dev/fliusbN) on the HOST — the module must be loaded on the host, not in
# the container. Run with --network host (for UDP discovery) and mount the
# device node and the built libfli.so:
#
#   docker build -t alpaca-fli .
#   docker run --rm --network host \
#     --device /dev/fliusb0 \
#     -v /path/to/libfli.so:/usr/local/lib/libfli.so:ro \
#     -v /path/to/config.yaml:/alpyca/config.yaml:ro \
#     alpaca-fli
FROM python:3.12-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends libusb-1.0-0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ ./src/
COPY config.yaml ./config.yaml

CMD ["python", "src/main.py"]
