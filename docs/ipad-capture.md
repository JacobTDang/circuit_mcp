# iPadOS screen capture

The harness ingests what is visible on iPadOS, independent of the application.
Sidecar is not an input source: it sends a Mac desktop to the iPad.

## Backends

AirPlay is primary. A separately built GPLv3 UxPlay process advertises
`Circuit Capture` through Bonjour, requires an ephemeral four-digit PIN, decodes
H.264 with GStreamer and renders into its headless `fakesink`. It creates no Mac preview window, so
closing or switching Mac applications cannot end the AirPlay session. The
draggable iPad card polls a transient decoded PNG so the live screen stays
inside the card. Snap explicitly saves a copy to the library; ordinary live
frames are never persisted. Unrelated displays and notifications are never included. The stream defaults
to `800x600@30`; override it with `CIRCUIT_MCP_AIRPLAY_SIZE`.

USB-C is the fallback. The native Swift helper opts into CoreMediaIO screen
capture devices, discovers external muxed AVFoundation devices, and writes one
PNG from `AVCaptureVideoDataOutput`. The iPad must be unlocked, trusted, and
connected with a data-capable cable. Camera permission belongs to the process
that launches the server.

Install both backends with:

```console
scripts/setup_ipad_capture.sh
```

The script builds UxPlay under ignored `.local/runtime`; it does not install
UxPlay globally or copy GPL source into the Python distribution.

## Lifecycle and privacy

- The receiver starts only through the UI or `ipad_receiver_start`.
- A new random PIN is generated each start unless
  `CIRCUIT_MCP_AIRPLAY_PIN` explicitly fixes one.
- No continuous recording is stored.
- `GET /api/ipad/frame` serves a no-store live frame and does not write a document.
- `capture_ipad_screen` chooses an active headless AirPlay stream, then USB-C.
- Frames are validated as PNG, bounded to 25 MiB, hashed, and timestamped.
- UI captures enter the local document database with source provenance.
- Stop the receiver with `ipad_receiver_stop` when finished.

## Troubleshooting

If USB reports no device, unlock the iPad, tap Trust, verify the cable carries
data, disconnect Sidecar, and grant Camera permission to Terminal/Codex before
restarting the server. AirPlay requires both devices on a network that permits
Bonjour/mDNS discovery; managed campus networks may block peer discovery.
