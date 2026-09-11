# Roadmap

Each of these is a separate piece of work with its own price. None of it is needed for what runs today.

**Video creatives.** Extract the frames that carry text, translate and replace the text on those frames, rebuild the video, and swap any subtitle track. Four of this advertiser's active ads are video, so this is the largest gap in the current version.

**Multi-country fan-out from one row.** One source ad, four target countries from the Settings tab, four output rows written in one pass instead of four rows pasted by hand.

**Batch runs.** Take a list of Ad Library links, or a whole advertiser page, and fill the sheet without a row having to exist first.

**Review queue.** A single view of every row sitting on Needs Review, with the before and after image side by side and an approve or reject action that writes the status back.

**Brand glossary column.** A per-row or per-account list of terms that must never be translated, must always be translated a set way, or must be spelled a set way. Product names and claim wording are the usual reasons.

**Official Ad Library API.** Replace the page read with Meta's sanctioned API for the copy fields, keeping the page read for the media URL the API does not return. Needs a registered developer app. It removes the risk that a markup change breaks the fetch.

**Draft upload to Ads Manager.** Push the finished copy and creative straight into your ad account as a paused draft, so the sheet stops being the last stop before a manual rebuild.
