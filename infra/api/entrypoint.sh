#!/bin/sh
set -eu

export PROMETHEUS_MULTIPROC_DIR=/tmp/lab-prometheus
mkdir -p "$PROMETHEUS_MULTIPROC_DIR"
rm -f "$PROMETHEUS_MULTIPROC_DIR"/*.db

exec "$@"
