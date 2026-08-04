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

## 2026-08-02 — Checkpoint 3: finding the actual annual reports

With a trustworthy list of "this company, this ticker, this year," the
next step was mechanical by comparison: look up each company's real filing
history and pick out the specific annual report that matches. This went
smoothly — matched 7,312 of the 7,403 confidently-resolved company-years
(99%) to a real, specific 10-K filing on file with the SEC.

Checked the 91 that didn't match, rather than just accepting the number:
64 of them are this year, 2026, and the reason is mundane — some companies
have a fiscal year that ends later in the calendar year than others, so
as of today they simply haven't filed their annual report yet. That's
expected and not a data problem. The other 27 are scattered thinly across
many different years and are presumably real gaps (a company delisted
partway through a year, a brief stretch of index membership, etc.) — we'll
account for those specifically rather than lump them in as "just missing"
once the fuller picture comes together.

Next: actually download these ~7,300 annual reports and pull out the
"Risk Factors" section from each one.

## 2026-08-02 — Checkpoint 4: pulling the actual risk factors text

Downloaded all ~7,300 annual reports and pulled out just the "Risk
Factors" section from each — the part of the report that matters for both
studies. This worked on the first pass for 98.5% of them, which is a
genuinely good outcome for text this varied: two decades of companies
using slightly different formatting, headings, and document structures.

Rather than stop there, we looked at the roughly 100 that didn't work, and
noticed something useful: it wasn't random. A handful of companies (Clorox,
Cincinnati Financial, Halliburton, U.S. Bancorp, Citigroup, Keurig Dr
Pepper) accounted for well over half the misses. Looking at one closely —
Johnson & Johnson's 2006 filing — explained why: the "main" document SEC
has on file for that filing is just a one-page table of contents pointing
to a different, separate document where the real annual report content
actually lives. That's an older-style filing convention some companies
used. We built a quick fix to check those other documents when the main
one comes up empty. It only recovered a handful more (6 out of 107) —
enough to confirm the theory was right, not enough to be worth chasing
further, so we stopped there rather than sinking more time into it.

Final result: 98.6% of all annual reports have their risk factors text
successfully pulled out. The remaining 1.4% will be accounted for
individually, with a specific reason each, later in the project — not
just written off as "missing" with no explanation.

Next: figure out what industry each company is in, so risk factors can be
compared and grouped sensibly (e.g., "how did banks talk about risk
differently from tech companies").

## 2026-08-02 — Checkpoint 5: sorting companies into industries

Every company files with the government under an old, standardized
industry code (from the 1970s-80s originally), which we used to sort all
546 companies into 11 broad, modern-style sectors — Technology, Financials,
Health Care, and so on. Built the mapping to cover far more codes than we
actually needed today, so a brand-new company showing up in a future year
won't fall into an "Unclassified" leftover bucket by default.

First pass classified everyone with none left unclassified, which sounded
great — but we checked the actual company list per sector rather than just
trusting the count, and found real problems. Almost a quarter of all
companies had landed in "Industrials," which was suspiciously high. Digging
in: a handful of well-known drug distributors (McKesson, Cardinal Health,
Henry Schein) had been swept into Industrials by a generic rule when they
clearly belong in Health Care. A bigger issue: 18 companies — including
Visa, Mastercard, PayPal, eBay, Etsy, Uber, and Accenture — all shared one
old, extremely vague government code literally named "Business Services,
Not Elsewhere Classified." That one code covers payment networks, online
marketplaces, and IT consultants alike, so no single sector was going to
be right for the whole group. We fixed the clear-cut cases individually by
name (Visa/Mastercard/PayPal are obviously financial companies; eBay/Etsy/
Uber are obviously retail-style marketplaces) and picked a more sensible
default for the rest. Second pass looks much more realistic.

Next: pull all of this together into the single most important piece of
this project — a complete accounting of every company-year, showing either
the actual risk factors text or the exact reason it isn't there.

## 2026-08-02 — Checkpoint 6: the complete accounting (the part we care about most)

Built the master table that answers, for every single company-year in the
entire 20-year study, one of two things: "here's the text" or "here's
exactly why not," with the "why not" being one of five specific,
plain reasons rather than a vague "missing." Crucially, this table starts
from the very first, most complete list we built (every company that was
ever in the S&P 500, every year) and works outward from there — not from
some later, already-filtered-down list. That distinction is exactly what
went wrong in the old project: its missing-data report was quietly blind
to a huge chunk of the real gaps because it only ever looked at data that
had already passed an earlier filter.

We checked the arithmetic obsessively, since this is the table the whole
project exists to produce: every count from every earlier step lines up
exactly with this final table, and the grand total matches the original
full list to the row. Nothing fell through a crack anywhere in the chain.

Along the way we caught a real bug worth calling out: the table initially
flagged seven large, obviously still-active companies — Applied Materials,
Micron, Procter & Gamble, Seagate, TE Connectivity, Tapestry, Western
Digital — as having "gone out of business and will never file again."
Obviously wrong, and worth understanding why: companies occasionally file
a specific government notice to deregister just ONE type of security (like
an old bond), and Applied Materials had done exactly that in 2018 — one
day before filing that year's completely normal annual report. Our check
was treating any such notice as "the whole company is gone," when really
we should only trust it if the company never filed anything again
afterward. Fixed, and all seven correctly show up as "hasn't filed yet
this year" instead (which is true — their fiscal years just haven't ended
yet).

Next: use this complete, accounted-for dataset to actually study something
— how companies talked about the 2025 Iran conflict, tariffs, and how
risk language has shifted over 20 years.

## 2026-08-02 — Checkpoint 7: does the data actually make sense?

Ran the real analysis: how often companies mention Iran-related topics,
tariffs, and Ukraine, and how the general tone and focus of risk-factor
writing has shifted over 20 years. All of this ran fast, since it's just
reading text files we'd already saved to disk — no more waiting on the
government's website.

The most reassuring part of this whole step: we checked the results
against things we already know happened in the real world, without
adjusting anything to force a match. If the pipeline were subtly broken —
matching the wrong year's filing to a company, or extracting the wrong
section of text — this is exactly the kind of check that would catch it.
It passed convincingly. Mentions of "pandemic" in these reports are flat
near zero for 15 years, then spike dramatically in reports filed in 2021
(which cover the first full year of COVID) and fade out again by 2024-2025
as the pandemic recedes. Mentions of "inflation" and "supply chain" follow
the exact same shape around the real 2021-2023 inflation surge. Mentions
of "cybersecurity" climb steadily starting around 2012 and never look
back, matching the well-known rise of cyber risk as a corporate concern.
And the reports themselves have gotten much longer over time — roughly
3x longer on average from 2006 to today — which matches what's widely
known about how much more detailed companies' risk disclosures have
become. None of this was something we tuned or nudged into place; it's
just what fell out once the underlying data pull was done correctly.

On the specific question that kicked off this project — how companies
talked about the 2025 Iran-Israel-US conflict — the honest finding is a
gradual rise in mentions over many years rather than one sharp jump right
at 2025. The reports covering 2025 itself (mostly filed in early 2026) do
show the highest count of Iran-related mentions in the entire 20-year
history, which is consistent with the conflict mattering, but it looks
like part of a longer trend (companies steadily disclosing more about
sanctions and geopolitical risk generally) rather than a single dramatic
spike.

Next: build the actual charts — this is where the Kieran Healy-style
visual design guidelines and the color-blind-safe palette come in,
including the tariffs-by-sector chart with the "still pending" hatch box.

## 2026-08-02 — Checkpoint 8: the charts, and getting the "still pending" box right

Built the actual visualizations: the Iran-mentions trend line, the
tariffs-by-sector stacked bar chart with the "still pending" hatch box,
and a set of small charts showing how risk-factor language has shifted
over 20 years.

The tariffs chart got special attention because this is the one the
project's own instructions specifically warned us about getting wrong.
The idea of the hatched box on top of each year's bar is "these are
filings that could still show up and make this year's number bigger,
because their deadline hasn't passed yet." It would be easy to
accidentally stuff every kind of missing data into that box — a company we
couldn't even identify, a company that never filed at all, a filing we
found but couldn't extract text from — but those are different problems
with different meanings, and mixing them in would make the box wildly
oversized and misleading. We used the careful reason-by-reason
missing-data table from the last checkpoint to make sure the box only
ever contains the "genuinely still pending" category, nothing else.

Once again, the results lined up with real history without any tuning:
tariff mentions were fairly flat and low through 2017, then rose sharply
starting in 2018 — exactly when the US-China trade war tariffs began.

Next: one final pass to make sure the whole dataset is internally
consistent, and spot-check a handful of specific tricky companies by hand
against the live SEC website.

## 2026-08-02 — Checkpoint 9: the final check

Did a last full pass to make sure everything adds up, and checked three
specific tricky companies by hand against SEC's live website, as planned
from the very start.

The bookkeeping checks out perfectly: every single company-year from the
original master list is accounted for in the final dataset, with no
duplicates and no contradictions (nothing marked both "found" and
"missing" at once).

The three hand-checks were genuinely useful, and honest about it:

- The recycled-ticker case (LB) correctly shows up as "we don't know,"
  never as a wrong guess. Exactly as intended.
- The merger case (Celgene, bought by Bristol Myers Squibb in 2019)
  revealed something worth being upfront about: Celgene is completely
  absent from our resolved dataset, even though we're confident (having
  checked by hand) that we could technically identify which company it
  is. The reason is a deliberate tradeoff made earlier in the project:
  we decided it was better to leave a company out entirely than to guess
  its identity using a method that's only right about 70% of the time.
  Celgene happens to be one of the correct 70%, but our system has no way
  to know that without a human looking at it, so it stays out. That's a
  real, known cost of choosing "don't guess" over "guess and sometimes be
  wrong" — not a bug, but worth stating plainly rather than glossing over.
- The recent-IPO case (GE Vernova, spun off from GE in 2024) mostly
  worked well — correctly identified for all three of its real years —
  but caught one genuine gap: its very first annual report writes the
  "Risk Factors" heading with quotation marks around it in a way our
  text-extraction pattern doesn't recognize. A real, specific, fixable-
  in-principle gap, now documented rather than quietly missed.

Bottom line: the dataset is honest about what it knows and doesn't know,
which was always the actual goal — not a perfect dataset, but one that
never silently pretends to know something it doesn't.

This completes the planned build. Everything is committed, checkpoint by
checkpoint, with the reasoning behind each choice recorded in
decisions.md.

## 2026-08-03 — Checkpoint 10: going back to fix two of the gaps we'd documented

After the build was "done," we went back and asked how hard it would
actually be to close the three biggest missingness categories
(`cik_never_resolved`, `year_specific_no_match`, `no_item_1a_extracted`).
Digging in turned up two real, fixable bugs rather than fundamental
limits:

- The CIK-resolution continuity check was only looking at the most
  recent ~1000 filings in SEC's own records for a company, which for a
  company that's been public a long time doesn't reach back far enough
  to cover an old year. That made the check wrongly reject companies
  that were actually fine the whole time. We confirmed this by hand
  against 116 affected tickers, and every single one turned out to
  still be trading under the exact same ticker today — proof these were
  false rejections, not real cases of a ticker changing hands.
- AEP (American Electric Power) was missing from one of SEC's own
  reference files entirely, even though it's a large, currently active
  company. A second SEC file has it. Checking both now costs nothing.
- Separately, the three tickers responsible for most of the leftover
  "couldn't extract Item 1A" failures (Clorox, Cincinnati Financial,
  Halliburton) turned out to each write the "Item 1A" heading in a
  slightly different, unusual way our text pattern didn't recognize yet.
  Once actually looked at side by side, all three turned out to be small
  variations on tolerance the parser already had for other words — just
  not applied to "Item 1A" itself.

Fixed all three, then did a full rebuild of the pipeline (not a patch)
so every downstream table would stay consistent, and re-ran the charts.
Net effect: total missingness dropped by about 7% (3313 -> 3079 missing
company-years). The CIK-resolution fix alone didn't recover as much as
first estimated, and that turned out to be a good sign rather than a
disappointment — spot-checking showed it correctly left alone the
companies where a ticker really was reused by a different, unrelated
company later on (DuPont's ticker "DD", Constellation Energy's ticker
"CEG"), while cleanly fixing the cases where it was genuinely the same
company the whole time (AMD, AEP). The fix did what it was supposed to
do, no more and no less.

One background hiccup worth noting for next time: the first attempt at
this rerun got silently killed partway through by something in the
environment, not by an error in the script. Restarting the remaining
steps with `nohup`/`disown` (so the process doesn't depend on the
original shell session staying alive) let it finish cleanly the second
time.
