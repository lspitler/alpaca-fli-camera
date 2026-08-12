# ASCOM Alpaca FLI server.
#
# libfli reaches FLI cameras through the fliusb kernel char device
# (/dev/fliusbN), and that module must be loaded on the HOST — the container
# needs nothing but the device node. Run with --network host so Alpaca UDP
# discovery works:
#
#   docker build -t alpaca-fli .
#   docker run --rm --network host \
#     --device /dev/fliusb0 \
#     -v /path/to/config.yaml:/alpyca/config.yaml:ro \
#     alpaca-fli
#
# ---------------------------------------------------------------------------
# libfli: built into the image WHEN the vendor SDK is in the build context.
#
# The FLI SDK is not redistributed in this repo (see .gitignore and
# sdk/README.md — download it from flicamera.com/support and unpack it as
# sdk/libfli-1.104/). So the first stage below builds libfli.so only if that
# source is present, and the build degrades honestly rather than failing:
#
#   * SDK present  -> /usr/local/lib/libfli.so is compiled in, which is exactly
#                     where config.yaml's default `library:` points. Talks to
#                     real hardware with nothing to mount.
#   * SDK absent    -> image still builds and is still useful for `demo: true`
#                     (the shipped default), ConformU and protocol work, which
#                     never load libfli. Connecting to real hardware will fail
#                     on a missing library; the build log says so loudly.
#
# Do NOT bind-mount a host-built sdk/libfli.so into the image, which is what
# earlier versions of this file advised. The server dlopens libfli through
# ctypes, so the object has to match the *container's* glibc rather than the
# host's; a host object built on a newer distro dies with
#
#   OSError: /lib/x86_64-linux-gnu/libc.so.6: version `GLIBC_2.42' not found
#           (required by /usr/local/lib/libfli.so)
#
# and — because the library is only loaded on PUT /connected, not at startup —
# the server log looks perfectly healthy while the failure reads like a camera
# fault. Mounting onto /usr/local/lib is doubly wrong: a volume there shadows
# the image's own Python installation. Running bare-metal is the case where a
# host build IS correct; use sdk/build_libfli.sh for that.
# ---------------------------------------------------------------------------

# Build libfli against the runtime image's own glibc. Deriving this stage from
# the same base as the final one is the point: it makes the ABI match by
# construction, so there is no "keep these two distros in sync" footgun.
FROM python:3.12-slim AS libfli
RUN apt-get update \
    && apt-get install -y --no-install-recommends gcc libc6-dev \
    && rm -rf /var/lib/apt/lists/*
COPY sdk/ /sdk/
# gcc is the entire toolchain requirement: on Linux libfli links against libc
# alone, reaching the camera through fliusb rather than libusb. Any libfli-*
# version directory works; build_libfli.sh takes the source dir via LIBFLI_SRC.
# If the tree carries libfli-grabrow-eio.patch applied, the resulting object
# reports a failed image download instead of returning a zero-filled buffer.
RUN mkdir -p /out \
    && if src=$(dirname "$(ls /sdk/libfli-*/libfli.c 2>/dev/null | head -1)") \
          && [ -f "$src/libfli.c" ]; then \
         echo "libfli: building from $src"; \
         cd /sdk && LIBFLI_SRC="$src" OUT=/out ./build_libfli.sh; \
       else \
         echo "############################################################"; \
         echo "libfli: vendor SDK not found in the build context."; \
         echo "  This image will run in demo mode but CANNOT drive real"; \
         echo "  hardware. To fix: download the FLI SDK from"; \
         echo "  flicamera.com/support, unpack it as sdk/libfli-1.104/"; \
         echo "  (see sdk/README.md), and rebuild."; \
         echo "############################################################"; \
       fi

FROM python:3.12-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends libusb-1.0-0 \
    && rm -rf /var/lib/apt/lists/*

# Copies libfli.so when the stage above built one, and nothing when it did not
# — a directory copy is what keeps both paths working, since COPY cannot be
# made conditional on a file's existence.
COPY --from=libfli /out/ /usr/local/lib/
# Only needed so a bare `library: libfli.so` resolves; the config default is an
# absolute path, which ctypes loads regardless of the cache.
RUN ldconfig

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ ./src/
COPY config.yaml ./config.yaml

CMD ["python", "src/main.py"]
