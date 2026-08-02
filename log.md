# Log (plain language)

## 2026-08-02 — Starting over, on purpose

We built this dataset once already, in a folder called
`13_EDGAR_10K_Risk_Sentiment`. That build worked, but along the way we found
and fixed a couple of serious bugs: the list of "which companies were in the
S&P 500 in which year" had gotten stuck on a single 2019 snapshot and was
silently reused for six years' worth of data, and a missing-data report was
undercounting how much data was actually missing by about 7x because it was
only checking part of the pipeline.

Rather than patch project 13 again, we're rebuilding from a clean folder
(`14_EDGAR_10K_Risk_Sentiment`) using everything we learned, so the new build
can't accidentally inherit an old cached file or an old script that has the
same blind spot baked in. Nothing from project 13 is being deleted — it stays
as the historical record of how we got here.

First thing we double-checked, independently this time (not just trusting our
own notes from before): which of two similarly-named files on GitHub is the
real, currently-maintained list of S&P 500 membership history. Checked both
files' edit histories directly — one has been edited as recently as mid-July
2026, the other hasn't been touched since November 2019. We're using the
actively-maintained one.

Set up the project: its own git repository, a Python virtual environment,
and the two tracking documents you're reading part of right now (this one in
plain language, `decisions.md` with the full technical reasoning).

## 2026-08-02 — Checkpoint 1: membership history fetched and it checks out

Pulled the full S&P 500 membership history (2006 through today, one
snapshot per year) and ran the sanity check we didn't have last time:
does every year actually look different from its neighbors? It does —
constituent counts move smoothly year to year (roughly 497 to 506 members,
which is normal since a handful of companies have two share classes each
with their own ticker), and no two adjacent years are identical copies of
each other. That was the exact failure that went undetected in the old
build, so seeing it pass here is a real, specific check, not a rubber stamp.

Also found a bonus resource in the same source: a table that lists, per
ticker, the exact date ranges it was actually in the S&P 500 — including
separate entries when the same ticker was used by the same company in two
different eras (like American Airlines' "AAL," which was in the index
under the old AMR Corp in the 1990s, dropped out during bankruptcy, then
came back years later after the US Airways merger — the data correctly
shows those as two separate stretches). This is going to be the key tool
for the next step: figuring out which specific company actually held a
ticker in a given historical year, since tickers get recycled by unrelated
companies over a 20-year window and a lot of tools only know who holds a
ticker *today*.

Next: use that per-ticker date-range table, cross-referenced with SEC's own
company-history records, to correctly identify the historical filer behind
every ticker in every year.

## 2026-08-02 — Checkpoint 2: figuring out who actually held each ticker, and learning when to stop trusting a clever trick

This step took much longer than expected, and the story of why is worth
telling plainly because it's the whole point of redoing this project.

The easy 70% of companies resolved cleanly: look up who holds a ticker
today, then double-check that they were actually around and filing
paperwork back in the target year (not a brand-new company that happened
to grab an old ticker later). That check alone correctly told apart real
cases like "DELL" meaning two different companies over time — the original
Dell that went private in 2013, versus the Dell Technologies that re-IPO'd
in 2018 under a different corporate registration.

For the remaining 30% — companies that got bought out, went private, or
otherwise disappeared and left their ticker up for grabs — there's no
current "holder" to look up at all. We tried a workaround: search the
government's filing database for 10-K reports that mention the ticker
symbol in their own text, on the theory that a company usually mentions
its own stock symbol somewhere in its annual report. This worked well for
some tickers (correctly found the original Dell, correctly found L Brands
in a general test) but when we ran it across everything and then
fact-checked a couple dozen results by hand, it was only right about
70-75% of the time. The wrong answers weren't obviously wrong, either —
they were real, ordinary companies that happened to mention the same
three-letter string somewhere in their own filing by coincidence (a
company called "Trump Entertainment Resorts" showed up as the answer for
ticker "TER," which should have been Teradyne, the electronics testing
company).

That's a genuinely bad trade: a guess that's wrong 1 time in 4, sitting
in the data with no flag distinguishing it from a guess we're actually
confident about, is worse than just admitting "we don't know." That was
the exact mistake behind the old project's biggest bug, so we're not
repeating a version of it here. The fix: keep the guess on file for later
review, but don't count it as a real answer. Right now, about 70% of all
company-years are confidently resolved, and the other 30% are honestly
marked "we don't know" with a specific, logged reason for each one —
rather than a falsely reassuring number that's secretly wrong a quarter
of the time.

Also worth noting: one of the early attempts at this search accidentally
searched across a company's *entire* multi-year membership window instead
of one year at a time, and that made the wrong-answer problem much worse
(searching Dell's whole 1996-2013 history in one go turned up a completely
unrelated company, "ABM Industries," as the "answer"). Narrowing the
search to a specific year fixed a lot of that, though not all of it — see
above.

Next: use these ~7,400 confidently-resolved company-years to find and pull
the actual 10-K filings.
