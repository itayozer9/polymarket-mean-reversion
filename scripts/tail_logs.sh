#!/usr/bin/env bash
cd "$(dirname "$0")/.."
exec tail -f logs/combined.log
