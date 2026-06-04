# Third-Party Licenses

Audio Transcriber is licensed under the MIT License (see [LICENSE](LICENSE)).
The Windows release bundles or depends on the third-party components below.
This file summarizes their licenses; consult each project for the
authoritative text.

## Bundled in the Windows release (.exe)

| Component | Version | License | Notes |
|---|---|---|---|
| FFmpeg (`ffmpeg.exe`, `ffprobe.exe`) | 8.1.1 essentials (gyan.dev) | **GPL v3** | Invoked as a separate process, not linked. See [`vendor/ffmpeg/LICENSE.txt`](vendor/ffmpeg/LICENSE.txt). Source: https://www.gyan.dev/ffmpeg/builds/ |
| CustomTkinter | 5.2.2 | MIT | UI toolkit |
| requests | 2.32.5 | Apache-2.0 | HTTP client |
| soundfile | 0.13.1 | BSD-3-Clause | audio I/O (libsndfile) |
| sounddevice | 0.5.5 | MIT | audio capture (PortAudio) |
| numpy | 2.2.6 | BSD-3-Clause | arrays |
| scipy | 1.15.3 | BSD-3-Clause | signal processing |
| psutil | 6.1.0 | BSD-3-Clause | process utilities |
| google-auth / google-auth-oauthlib / google-api-python-client | 2.46.0 / 1.3.0 / 2.196.0 | Apache-2.0 | Google Drive backup |
| markitdown[docx,pdf,pptx,xlsx] | 0.1.6 | MIT | reference-document → Markdown grounding |
| magika | (transitive) | Apache-2.0 | local file-type detection |
| onnxruntime | (transitive) | MIT | local CPU ONNX runtime (used by magika) |
| pandas | (transitive) | BSD-3-Clause | xlsx parsing (markitdown) |
| pdfminer.six / python-docx / python-pptx / openpyxl / lxml | (transitive) | various permissive (MIT/BSD/Apache) | document parsers (markitdown) |

## Note on FFmpeg (GPL v3)

The vendored FFmpeg build is **GPL v3** (configured with `--enable-gpl
--enable-version3`). It is bundled as a standalone executable and invoked
via `subprocess` — the Audio Transcriber Python source is not a derivative
work of FFmpeg and remains MIT-licensed (mere aggregation). To comply with
the GPL, the release ships [`vendor/ffmpeg/LICENSE.txt`](vendor/ffmpeg/LICENSE.txt)
(license identification + written offer for corresponding source) alongside
the binaries.

FFmpeg corresponding source: https://www.gyan.dev/ffmpeg/builds/ ·
https://git.ffmpeg.org/ffmpeg.git
