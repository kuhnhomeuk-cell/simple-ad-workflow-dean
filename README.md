# Ad translation pipeline

Set a row to `Translate`, pick a target country, paste a Facebook Ad Library link. The engine claims the row, fetches the ad, rewrites the primary text, headline and description into the target language, and translates any text burned into the creative. It uploads the image to a country folder in Drive and writes the fields, the link, the language and a final status back into the row. Anything it is not sure about lands on `Needs Review` with the reason written in plain words, outputs and all.

The sheet is the whole interface. There is nothing else for you to open.

Loom of a run: [Loom link to be added]

## The four rows

Source ads are from advertiser Valérie.L / Avenor Paris, French. Target Netherlands, nl-NL.

| ID | Ad | What was on the image | Result | Status |
| --- | --- | --- | --- | --- |
| NL-027 | 1059406850353566 | No text. A photograph. | Three copy fields in Dutch, roughly 1,000 words of story text. Image uploaded as-is. | Finished |
| NL-028 | 27369494302749728 | Seven French strings on a designed banner | All seven replaced in Dutch and verified on the output. No copy: the Ad Library shows this ad as multiple versions with no primary text, headline or description. | Needs Review |
| NL-029 | 934283416402786 | Three lines of French handwriting on a pink sticky note | All three replaced in Dutch and verified on the output. Three copy fields in Dutch. | Finished |
| NL-030 | 1059406850353566 | No text. A photograph. | Duplicate of the NL-027 row. Same copy, same image. | Finished |

NL-029 and NL-030 had blank ID cells. The engine allocated those two IDs at claim time, taking the `NL` prefix from the target country and the next free number for that prefix.

NL-028 is the one worth a minute. The copy fields are empty because the Ad Library gave us nothing to translate, and the row says so instead of filling them. A pipeline that invented three fields of Dutch ad copy and marked the row Finished would look better in this table and be worse to own.

## Examples

Each folder holds the French source fields, the Dutch output, the before and after image, and a short note on what changed.

- [`examples/NL-027/`](examples/NL-027/): textless photo, copy only
- [`examples/NL-028/`](examples/NL-028/): seven strings on a designed creative, no source copy
- [`examples/NL-029/`](examples/NL-029/): handwritten sticky note

## How it works

- [`docs/architecture.md`](docs/architecture.md): the flow with every branch drawn, how the ad is fetched, the sheet contract, which models run which step, what an ad costs
- [`docs/costs.md`](docs/costs.md): per-ad estimate and monthly figures at 50, 200 and 1,000 ads
- [`docs/roadmap.md`](docs/roadmap.md): video, multi-country fan-out, batch runs, review queue and the rest, one line each
- [`diagrams/`](diagrams/): three diagrams as standalone pages, open them in a browser: the pipeline per row, the image branch, and the row status machine

## When it goes wrong

[`docs/failure-handling.md`](docs/failure-handling.md) covers it. The claim lock stops two runs colliding. A run that dies part way through a row is reset after thirty minutes. Each of the three statuses gets a section saying what it means and what its note reads like.

It also covers the read-back that stops a bad image edit being marked Finished, the character limits on the headline and description, and how video, carousels and multi-version ads are handled.

Read that one before the architecture. It is the part that decides whether you can leave this running.

## Reusing another advertiser's work

[`docs/legal-note.md`](docs/legal-note.md). One paragraph, worth reading before you publish anything this produces.

## Working together

This repo is the proof and the spec. It shows you the outputs, the design and the failure behaviour so you can judge the work without taking our word for it.

The pipeline itself is running now and we operate it. Send us rows and we will run them.

The full source, the prompts and a handover session ship on payment, at which point you run it yourself on your own account. Price: [price to be agreed].

What is deliberately not in this repo: the fetcher, the prompt files, the read-back logic and every credential. That is the engine, and it stays ours until the handover.
