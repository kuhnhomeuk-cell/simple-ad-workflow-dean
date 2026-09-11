# What happens when it goes wrong

Your sheet already has Processing, Needs Review and Failed in the Status dropdown. None of them were wired to anything. This is what each one now means.

## The claim lock

Two runs can overlap. A cron firing every five minutes on a row that takes ninety seconds is fine, but a manual run started while the cron is mid-row is not.

Before touching a row, the engine writes `Processing` into its Status cell and reads it back. If the value it reads is not the one it wrote, another run got there first and this one skips the row without touching a single cell.

This is why `Processing` appears in your sheet the moment a row starts. It is the lock, not a progress bar.

## The stale reset

A run can die between claiming a row and finishing it: the machine sleeps, the network drops, the process is killed. The row is then stuck on `Processing` and no later run will claim it, because the lock is still held.

At the start of every run the engine looks for rows that have been `Processing` for more than thirty minutes and sets them back to `Translate`. They get picked up on the next pass.

Thirty minutes is long enough that a slow but healthy row is never interrupted, and short enough that a dead run costs you one cycle.

## The three statuses

**Finished.** Every field is written, the Drive link works, the image carries the target language where it carried text. Review Note is empty. Nothing needs you.

**Needs Review.** The outputs are written and the Drive link works, but something about the result needs a person's eye. Review Note says what, in one sentence. Rows reach this status for reasons like:

- `no copy on the source ad: Ad Library shows multiple versions with no primary text, headline or description`: row NL-028 in this package
- `video creative: not handled in this version`
- `image edit could not be verified: target text not found in the output after one retry`
- `description trimmed to fit 125 characters`: one clause dropped, nothing added
- `headline at the 40 character cap`

The note names the specific thing. It never says "review needed".

**Failed.** Nothing usable was produced. Review Note carries the cause, again in one sentence: `ad 1234567890 not found in the Ad Library`, `Facebook Ad Link cell is empty`, `Target Country "Belgium" is not in the Settings tab`. A Failed row is safe to re-run once the cause is fixed. Set it back to `Translate`.

The line between the last two matters. Needs Review means you got something and should check it. Failed means you got nothing.

## Image read-back

An image model that is asked to replace text can drop a line, leave the original in place, or produce something that looks right at a glance and is not.

So the engine does not trust the edit. After the image comes back, a separate model read looks at the finished file and reports what text it can see. Every target string has to be found. If one is missing, the edit runs once more. If it is still missing, the row goes to Needs Review with the note saying so, and you get the original and both attempts.

This ran on both edited creatives in this package. NL-028's seven strings and NL-029's three handwritten lines were each confirmed on the output before the rows were marked Finished.

A bad edit can still be produced. It cannot be marked Finished.

## Length gates

Meta truncates a headline past 40 characters and a description past 125. Dutch runs longer than French, so a faithful translation can overflow a source that fits.

The engine counts both fields after translating. Over the cap, it asks for a shorter version of the same claims rather than cutting mid-word, then counts again. If the shorter version has dropped a claim, the row goes to Needs Review with the dropped clause named.

NL-029 in this package shows the case. The Dutch description landed at 125 characters with one clause of the French dropped, `lèvres douces toute la journée`. The note records it.

The primary text has no cap and is never trimmed. The longest source here ran to about 1,000 words and came through whole.

## Video creatives

Four of this advertiser's active ads are video. They are out of scope in this version.

A row whose creative is an mp4 gets its copy translated normally, then goes to Needs Review with the note `video creative: not handled in this version`. You still get the three Dutch fields. You do not get a translated video.

This is a deliberate stop rather than a crash. The roadmap prices the work.

## Carousels

An ad with several images in one card has one set of copy and several creatives. This version handles the first creative and flags the row: `carousel creative: first image handled, N further images not handled`. The copy is correct either way, because a carousel carries one primary text.

## Ads with multiple versions

Meta lets an advertiser run several variants under one Library ID. When it does, the Ad Library page often shows the creative but no primary text, headline or description, because there is no single version to show.

The engine reports this rather than working around it. The image is fetched and translated, the copy fields stay empty, and Review Note says the source carried no copy.

Row NL-028 is this case. The seven strings on the creative were translated and verified. The three copy fields are blank because the Ad Library gave us nothing to translate. Filling them would mean inventing ad copy and attributing it to the source advertiser.
