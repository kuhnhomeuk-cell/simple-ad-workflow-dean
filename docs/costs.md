# What a run costs

## Today's four rows

Nothing per ad. The copy ran through an existing Claude subscription and the image edits through an existing Grok Imagine subscription. No API key was used, so no per-ad charge was incurred.

That path needs a person at the keyboard. It is fine for a batch you are watching and wrong for a cron job.

## The API path

These are the numbers for running it unattended. They are an **estimate** built from published per-token and per-image prices, not a measured invoice. Treat them as the order of magnitude, not the bill.

| Step | Runs on | Work | USD |
| --- | --- | --- | --- |
| Copy transcreation | Claude, large model | ~4k tokens in, ~3k out | ~0.10 |
| Copy judge | Claude, small model | ~6k tokens in, ~0.3k out | ~0.02 |
| Image text detection and read-back | Gemini Flash vision | 2 image reads | ~0.01 |
| Image edit, only when the creative has text | Nano Banana Pro | 1 to 2 attempts | 0.04 to 0.20 |

**Per ad: 0.13 USD with no text on the creative, up to 0.35 USD when the image has to be edited.**

The spread is the image edit. A photo with no text skips that step entirely. A designed creative that needs a second attempt sits at the top of the range.

In this package, NL-027 is the cheap case and NL-028 and NL-029 are the expensive one. Two of the three needed an image edit, which is a higher share than we would expect across a whole account.

## Monthly

| Ads per month | All cheap, 0.13 each | All edited, 0.35 each |
| --- | --- | --- |
| 50 | 6.50 USD | 17.50 USD |
| 200 | 26.00 USD | 70.00 USD |
| 1,000 | 130.00 USD | 350.00 USD |

Real usage lands between the two columns, at whatever share of your ads carry text on the creative.

Not in these figures: the machine the engine runs on, Google Drive storage, and the fallback scraper if Meta changes its page markup. The scraper is roughly 0.75 USD per 1,000 ads and is off unless it is needed.

Also not in these figures: our time. That is the engagement, and it is in the README.

## The lever, if the number matters

Most of the copy cost is the large model on the transcreation. Switching it to the smaller model takes the copy step from about 0.10 to under 0.02 USD and the per-ad floor from 0.13 to about 0.05.

We have not done that, because the copy is the product. A story ad of a thousand words that reads as machine-made will not perform, whatever it cost to produce. The model is one line of configuration, so you can see both and pick.
