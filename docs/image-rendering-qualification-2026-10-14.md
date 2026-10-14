# Optional image pipeline qualification

Issues #40–#43 implement post-text rendering, saved image artifacts, an outcome
manifest, and the run gallery. The implementation follows the current official
[AUTOMATIC1111 API](https://github.com/AUTOMATIC1111/stable-diffusion-webui/blob/master/modules/api/api.py).
Text exports are saved first so an image-server outage cannot remove a completed
manuscript. No writing calls occur during the serial image batch.

Tests exercise a real local HTTP server with synthetic PNG responses, database
round trips, decoded image/manifest downloads, PNG/JPEG/WebP encoding, missing
metadata and invalid outputs, partial failures, timeout/busy-server handling,
stable seed recovery, attempt exhaustion, missing-file rebuilding, changed
source/settings history, cover candidates, cancellation, and final pipeline
acceptance/export ordering. Text-only controls make no image-server requests.

An isolated app/database was opened in the in-app browser. The gallery showed a
completed thumbnail with natural dimensions 64×96, a failed request, and a chapter
with no prompt marked skipped. Image and sidecar links and the manifest download
were present; the browser reported no console errors. The fixture is a flat-color
protocol test image, not model-generated illustration quality evidence.

Local evidence is under `artifacts/qualification/post-text-images-2026-10-14/`.
The production Orchard manuscript and its running revised edition were not used
as image fixtures or altered for rendering. Image settings remain disabled there.

No image model/server was installed or qualified in this change. Local VRAM fit,
composition, identity continuity, and publishable image quality require a later
real-backend qualification. A timed-out POST has an unknown server outcome; the
app skips the remaining batch and requires an idle server before bounded recovery.
