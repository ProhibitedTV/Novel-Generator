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

When image stages are enabled, extraction also runs automatically after final text
acceptance and manuscript export. An optional extraction failure leaves the text
and exports intact. Rendering, image files, manifests, and the image gallery remain
separate outstanding features (#40 through #43).

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
packs. This file is extraction evidence, not a rendered-image manifest.

Qualification uses a real local HTTP test server, database round trips, artifact
downloads, malformed/absent metadata, stale-source UI, and the pipeline's final
acceptance/export path with images enabled and disabled. Actual image rendering
has not been qualified.
