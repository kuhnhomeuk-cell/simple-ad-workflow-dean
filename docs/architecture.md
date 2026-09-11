# Architecture

## The flow

Your Miro board has six boxes: sheet row, fetch the ad, translate the copy, translate the image, upload to Drive, write back. Those six are right and they are the spine of the engine. What the board does not draw is every branch off that spine, and the branches are where the work is.

Here is the same flow with the branches in.

```
Sheet row, Status = Translate
        |
        v
  Claim the row  ------ already claimed by another run --> skip, touch nothing
        |
        v
  Allocate an ID if the ID cell is blank  (prefix from Target Country)
        |
        v
  Fetch the ad from the Ad Library by Library ID
        |
        +---- video creative ----------> Needs Review, note says so
        +---- no copy on the ad --------> carry on, flag the reason
        |
        v
  Transcreate primary text, headline, description
        |
        v
  Judge the copy (second model, independent read)
        |
        +---- length over the cap ------> trim or flag
        +---- claim missing or added ---> flag the reason
        |
        v
  Does the creative carry text?
        |
        +---- no ----> use the image as-is
        |
        +---- yes ---> translate the strings
                            |
                            v
                       edit the image in place
                            |
                            v
                       read the output back
                            |
                            +---- target text not found --> retry once, then Needs Review
        |
        v
  Upload <ID>.png to the country folder in Drive
        |
        v
  Write the fields, the Drive link, the language and the status
        |
        v
  Finished  |  Needs Review  |  Failed
```

Deterministic Python does the claiming, the ID allocation, the fetching, the uploading and the writing back. Models do two judgment steps, translating the copy and editing the image, and a third model read checks the second one's output.

Every branch that ends in Needs Review still writes whatever it produced. A flagged row is a row with outputs and a reason, never an empty row.

## How the ad is fetched

The engine opens `facebook.com/ads/library/?id=<Library ID>` in a headless browser and reads the ad block keyed on that ID. Primary text, headline, description, the call to action, the landing domain and the creative URL are all in the page.

One detail worth recording, because it cost time to find. Where an ad appears both as a listing card and behind the "Link to ad" dialog, the engine reads the dialog. The listing card for ad 1059406850353566 carries no link card at all, while the dialog carries the full record. Reading the dialog works for both shapes, so the engine always does.

Meta's official Ad Library API is the better long-term source: its text fields are stable and it is the sanctioned route. It returns no media URL and it needs a registered developer app, so it is a hardening step rather than the starting point. The page read needs no setup and it works today.

If Meta changes the page markup, the fetch is one interface with one implementation behind it. A hosted scraper can be dropped in behind the same interface at roughly 0.75 USD per 1,000 ads.

## The sheet contract

The engine reads columns by header name and never by column letter, so you can reorder or insert columns without breaking it. It refuses to start when a required header is missing rather than writing into the wrong place.

Tab `Sheet1`:

| Column | Read or written | What it is |
| --- | --- | --- |
| ID | Read, written when blank | The row key. Allocated at claim time from the country prefix. |
| Status | Read and written | The trigger and the result. |
| Target Country | Read | Looked up in the Settings tab for the language. |
| Store / product URL | Neither | Passed through untouched. |
| Trendtrack Ad Link | Neither | Passed through untouched. |
| Facebook Ad Link | Read | The Library ID is taken from this URL. |
| Drive URL | Written | Link to the uploaded creative. |
| Primary Text | Written | Transcreated. |
| Headline | Written | Transcreated, capped at 40 characters. |
| Description | Written | Transcreated, capped at 125 characters. |
| Target Language | Written | From the Settings tab. |
| Review Note | Written | Empty on Finished. Carries the reason otherwise. |

Tab `Settings`, the locale contract:

| Target Country | Default Language | Currency |
| --- | --- | --- |
| United States | English (en-US) | USD |
| Brazil | Portuguese (pt-BR) | BRL |
| Netherlands | Dutch (nl-NL) | EUR |
| Sweden | Swedish (sv-SE) | SEK |

Adding a country is a row in this tab, not a code change.

## The model steps are pluggable

The deterministic parts of the engine are Python and they do not change. The model steps sit behind interfaces, so which model runs a step is configuration.

Today's four rows ran through a Claude Code session for the copy and Grok Imagine for the image edit, both on existing subscriptions. No API key was used and no per-ad API charge was incurred. That path needs a person at the keyboard.

The drop-in API path, for when you want it running unattended on a cron, is Claude for the copy and Google's image model for the edit. Same interfaces, same outputs, no change to the sheet. That path is the one the cost estimate below prices.

## The two image models

Two models can do in-place text replacement on a designed creative well enough to ship.

**Grok Imagine** is what ran today. It handled both cases here: seven set strings on a designed banner, and three lines of handwriting on a sticky note. Handwriting is the harder of the two and the one your second Loom asked about. It runs through a subscription rather than an API, so it needs a person.

**Nano Banana Pro**, Google's image model, is the API path. It is built for in-place text editing and it runs unattended. Roughly 0.04 to 0.20 USD per image depending on how many attempts a creative takes.

Higgsfield, which you named in the second Loom, is not used. We could not confirm it has a documented API, so we could not build on it. If you have an account and it does, it drops into the same interface as the other two.

Whichever model runs the edit, the read-back gate is the same: a separate model read of the finished file has to find the target text before the row can go Finished.

## Cost per ad

Today's four rows cost nothing per ad beyond the subscriptions that were already running.

On the API path, the estimate is **0.13 USD for an ad with no text on the creative and 0.35 USD for one that needs an image edit**. This is an estimate built from published token and image prices, not a measured bill. `costs.md` has the breakdown and the monthly figures.
