# Optional local image foundations

Use **Images** in the app navigation (`/settings/images`) to save image settings.
They are disabled by default and stored in the application database shared by the
web process and worker. Width, height (multiples of 8), steps, sampler, CFG scale,
random/fixed seed mode, fixed seed, and PNG/JPEG/WebP output preference are saved.

The connection test supports an AUTOMATIC1111-compatible API. Start that server
with `--api`; test GET `/sdapi/v1/options` and `/sdapi/v1/samplers` against the
[official API implementation](https://github.com/AUTOMATIC1111/stable-diffusion-webui/blob/master/modules/api/api.py).
The result checks a configured model checkpoint and the selected sampler. An HTTP
200 response from an unrelated server is not considered ready. The test has a
five-second timeout per request, never renders images, and never changes server
settings. It is explicit: ordinary settings reads, text generation, and health
checks do not contact the image server.

For the app in Docker and an image server on Windows, the saved URL typically
needs `http://host.docker.internal:7860`. The image server must listen on an address
reachable from Docker. The default loopback URL suits an app running directly on
the host. This implementation does not install an image server or a checkpoint.

API endpoints:

- `GET /api/image-generation/config`: read effective saved settings.
- `PUT /api/image-generation/config`: replace settings with a validated JSON object.
- `POST /api/image-generation/test`: test the saved server without rendering.
- `POST /api/runs/{id}/image-packs/extract`: explicitly extract metadata from a completed manuscript.

When image stages are enabled, extraction and sequential rendering run automatically
after final text acceptance and manuscript export. Optional failures leave the text
and exports intact. Rendering uses the AUTOMATIC1111-compatible
[`/sdapi/v1/txt2img` API](https://github.com/AUTOMATIC1111/stable-diffusion-webui/blob/master/modules/api/api.py).
No rendering request is made for a chapter without a usable pack. A current,
source-bound cover prompt pack, when present, also supplies front and optional back
cover candidates. Image settings alone do not create missing illustration prompts.

## IMAGE PACK format

The extractor reads explicit metadata already present in chapter or guidebook-entry
text. It does not ask the writing model to add metadata, modify accepted text, or
render images. Do not insert blocks into an already accepted novel just to exercise
the parser: changing prose correctly invalidates acceptance.

```text
## IMAGE PACK
Primary prompt: A relief map of the valley with a river and terraced orchards.
Negative prompt: letters, logos, watermark
Suggested aspect ratio: 16:9
Key visual elements:
- winding river
- terraced orchards
Continuity tags: valley-map, autumn
Seed: 123
Image count: 1
```

Heading blocks end at the next heading or end of chapter. `[IMAGE PACK]` blocks
can end with `[END IMAGE PACK]`; HTML comment blocks and `image-pack` code fences
are also supported. A JSON object under an IMAGE PACK heading can use
`primary_prompt`, `negative_prompt`, `aspect_ratio`, `key_visual_elements`,
`continuity_tags`, `seed`, and `image_count`. Lists can be bullets, comma/semicolon
separated text, or JSON arrays. Seeds and image counts are optional (defaults:
no seed override, one image); required fields are never fabricated.

Each chapter gets an entry in a downloadable `image-packs-<fingerprint>.json`
artifact with its number, title, source hash, all parsed packs, and warnings.
Invalid blocks remain visible as warnings; chapters without blocks explicitly
request no images. The same source reuses its artifact; a source change marks the
saved pack stale. The run page shows warnings and prompt metadata for current
packs. This file is extraction evidence. The separate rendering manifest records
actual render outcomes.

Qualification uses a real local HTTP test server, database round trips, artifact
downloads, malformed/absent metadata, stale-source UI, and the pipeline's final
acceptance/export path with images enabled and disabled. An actual installed image
model has not been qualified. HTTP protocol and browser qualification use synthetic
image fixtures; they do not establish illustration quality or local GPU readiness.

## Rendering and recovery

Set a render timeout (10–1800 seconds; default 600) and an attempt limit (default
two across resumptions) in image settings. Images run one at a time with batch size
and iteration count one, after all writing and cover planning calls. The stage
does not install an image server/checkpoint or unload other resident GPU models;
the image backend must have sufficient memory or support offloading.

The selected aspect ratio fits inside the saved maximum width and height, rounded
to multiples of eight. A ratio that cannot fit at least 64 pixels on both sides is
skipped with a warning. Random seeds are chosen and persisted before requests.
For multiple images in a pack, explicit/fixed base seeds advance by image index
(wrapping at 2^32), providing reproducible variations. The detected checkpoint is
pinned with a per-request override that restores the server's prior settings.

Each exact source/settings/prompt batch has a directory:

```text
artifacts/<run-id>/images/<fingerprint>/
  image_manifest.json
  chapter_001_pack_01_image_01.png
  chapter_001_pack_01_image_01_prompt.json
  cover_<concept-id>_front.png
```

PNG, JPEG, and WebP outputs are decoded, dimension-checked, and encoded in the
selected format. Each successful image and JSON sidecar is a downloadable run
artifact. Sidecars and manifest entries include prompts, effective seeds,
dimensions, server/model settings, exact API payloads when submitted, source
hashes, and generation metadata. The manifest also retains missing, invalid,
pending, failed, and skipped requests; warnings are never represented as renders.

Atomic checkpoints follow each image. Worker recovery reuses verified completed
files and only retries unfinished/failed images within their saved attempt limit.
Missing or changed saved files are visible and can be rebuilt. A changed source,
prompt, or configuration creates a new batch; prior manifests/images remain
historical artifacts. Current acceptance is required again on resume.

After a timeout, the server's outcome is unknown: the remaining requests are
skipped. Before any resumed POST, the app requires `/sdapi/v1/progress` to report
an idle server. An interrupted POST cannot guarantee exactly-once rendering;
the API has no durable request/result recovery contract. Its persisted seed
allows a bounded rerender after the server is idle.

For explicit recovery of a completed book, stop the worker while it is idle and
run `python -m novel_generator.services.image_rendering --run-id <id>` in the
same configured environment, then restart the worker. The command refuses an
active text run. The manifest's request payloads also support manual rerendering.
Do not run recovery alongside another rendering process.

The run page groups thumbnails by chapter and cover concepts, provides individual
image/sidecar and manifest downloads, and shows failed/skipped states. Changed or
unaccepted text hides current thumbnails and labels saved outputs as historical.
Text-only runs have no image manifest or gallery and never contact an image server.
