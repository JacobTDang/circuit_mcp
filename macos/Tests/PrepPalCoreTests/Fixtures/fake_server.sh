#!/bin/sh
# Stands in for `python -m circuit_mcp.app_server` in ServerController tests.
case "$1" in
  ready)         echo "INFO: starting"; echo "READY 45678"; exec sleep 300 ;;
  locked)        echo "LOCKED 4242"; exit 3 ;;
  # uvicorn's own startup failure also exits 3, but without a LOCKED line.
  locked-silent) exit 3 ;;
  malformed)     echo "READY soon"; exec sleep 300 ;;
  silent)        exec sleep 300 ;;
  # READY arrives at about the ready deadline, so the two contend.
  late)          sleep 1; echo "READY 45682"; exec sleep 300 ;;
  die-early)     echo "Traceback: boom" >&2; exit 1 ;;
  crash)         echo "READY 45679"; sleep 0.3; exit 7 ;;
  stubborn)      trap '' TERM; echo "READY 45680"; sleep 300 & wait ;;
  with-child)    sleep 300 & echo "CHILD $!"; echo "READY 45681"; trap 'exit 0' TERM; wait ;;
  *)             echo "fake_server: unknown mode '$1'" >&2; exit 64 ;;
esac
