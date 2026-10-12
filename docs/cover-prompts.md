# Cover prompt packs

Cover planning uses final manuscript excerpts, the story bible, and the author
brief to create a structured JSON artifact. It supplies one primary cover and at
least two alternatives, with distinct imagery or composition, portrait aspect
ratios, typography-safe areas, negative prompts, genre signals, image continuity
tags, and optional back-cover backgrounds. Every visual motif has an exact
quotation and a source excerpt reference.

The exported front-cover prompts include instructions to leave the title and
author areas empty. Artwork should contain no rendered lettering, logos, or
watermarks; typography can be added separately. The pack describes artwork and
does not render images. The later image batch renderer can consume each concept's
`front_cover_prompt`, `negative_prompt`, and `aspect_ratio` directly.

## Automatic generation

Set `GENERATE_COVER_PROMPTS=true` and restart the worker to generate a pack after
final text acceptance and manuscript export. The default is false, keeping this
optional step separate from the text workflow. The **Book cover prompts** task
route can select a different installed local model; without an override, it uses
the run's default provider and model.

Cover planning requires all requested chapters in book order and completed
Markdown and Word exports. Autonomous runs also require a final acceptance event
whose manuscript hash matches the current prose. A cover failure records a
diagnostic and preserves the accepted text, exports, and normal book completion.
Cancellation remains available.

## Existing completed books

Run the following in the configured project environment:

```powershell
.\.venv\Scripts\python.exe -m novel_generator.services.cover_prompts --run-id YOUR_COMPLETED_RUN_ID
```

For the local Docker installation, use the web container so the command inherits
the application's database, artifact directory, and provider settings:

```powershell
docker compose -f docker-compose.yml -f docker-compose.local.yml exec -T web python -m novel_generator.services.cover_prompts --run-id YOUR_COMPLETED_RUN_ID
```

The command prints the saved artifact path. The normal run artifact list supplies
its download. A matching valid pack is reused; source, contract, or route changes
require regeneration. The JSON records the manuscript hash and model provenance.
Previous successful packs are retained as history when a new pack replaces them.

## Scope and verification

Art direction receives explicitly labeled first, middle, and ending paragraph
excerpts from every chapter. Sampling has a bounded character allowance for long
books; it does not claim to provide the entire manuscript to the cover model.
Quotes and excerpt IDs are validated against the selected source. Cover concepts
and genre signals remain model judgments, and this feature does not certify the
book's quality or an eventual rendered cover.

The October 12 local qualification generated three concepts for the completed
1,375-word Apricot Boundary sample using Gemma 4 12B. One automatic validation
correction produced valid source references. The saved pack had three evidenced
motifs, three distinct concepts with portrait ratios, and title/author safe-zone
instructions. A second invocation reused the artifact. The accepted manuscript
hash and every existing text export remained unchanged. Workstation evidence is
retained under `artifacts/cover-prompt-qualification-20261012/`; no images were
rendered and this test does not qualify the full novel's cover.
