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

## 2026-08-16 — Finding the identification bug, and three fixes

The question that started this was simple: how many companies do we have
per year, and how many of them do we actually have risk factors for? The
answer had an odd shape — coverage climbed from 48% in 2006 to 91% in
2025 — and that climb turned out to be mostly an artifact of our own
code, not anything about how companies file.

Almost the entire gap was one step: figuring out *which company* a ticker
belonged to in a given year. Once a company is correctly identified,
finding its annual report is essentially solved — only 97 rows out of
7,578 were lost at that stage. Everything else was identification.

**The bug.** To decide whether today's holder of a ticker is the same
company that held it back then, we checked whether that company was
filing paperwork at the start of its S&P 500 membership. For most
companies that start date is January 1996 — a decade before our study
even begins. So any company that reorganized at some point in between
failed the test for *every single year*, including recent years where
there was no ambiguity at all. Oracle failed all 21 years. So did
Comcast, ConocoPhillips, Duke Energy, Con Edison, Exelon, Moody's,
Newmont, Northrop Grumman, FedEx. FedEx missed the cutoff by ten months
and lost two decades of data.

We measured it against the government's own records before touching any
code: 503 of 883 rejections were flat-out wrong. The fix asks the
question about the year we actually care about instead of about 1996.
It recovered 500 rows — within three of the estimate, which is a good
sign a fix does what you think and nothing else.

**The second fix is about the difference between guessing and checking.**
For companies that no longer exist, we'd been searching annual reports
for the ticker symbol and taking the best match, which an earlier audit
found was right only about 70% of the time. The trap is a company like
Trump Entertainment Resorts, whose report abbreviates itself "TER"
constantly — it outranks Teradyne, whose actual ticker that was. No
amount of better ranking fixes that.

But you can just *ask each candidate* which symbol it claims. Trump
Entertainment says its symbol is TRMP, so it's not merely outranked, it's
ruled out. Checking the top three candidates instead of only the first,
and letting that evidence decide, recovered about 1,100 more rows — and
positively ruled out 1,142 candidates that the old approach would have
accepted as correct. That second number is the real result: those were
wrong answers waiting to happen.

Where the evidence genuinely doesn't exist, we say so. Before 2019 the
government didn't require companies to print their ticker on the cover
page, and plenty didn't — Teradyne's own 2010 report never contains the
string "TER" at all. Those years stay marked as gaps rather than guesses.

**The third fix is a hand-maintained list**, for identities no automated
method can reach — usually where a ticker now points at a *newer*
corporate entity than the one that filed the reports. Exxon is the
cleanest example: the ticker XOM currently maps to a 2026 holding company
that has never filed an annual report in its life.

Since this is the one place a person writes an identity in directly, we
split proposing from accepting: anyone can propose, but nothing gets
written until the government's own filings confirm it. Of 22 proposed,
21 confirmed and one — Xerox — was contradicted by the evidence and left
out. A guard that rejects the proposals of the person who wrote it is
the only kind worth having.

That check also caught a subtler mistake of ours. Baker Hughes really
does claim the ticker BHGE, so the identity was right — but the company
didn't exist until 2017, and we'd written the date range as starting in
2006. Confirming *who* a company is doesn't confirm *how long* it was
around. Eleven years would have been quietly assigned to a company that
didn't exist yet, with a perfectly real-looking verification note
attached. Now the range gets bounded by actual filings, and the main
script double-checks every year independently.

**Where we ended up:** 9,296 risk-factor sections, up from 7,445 — an
extra 1,851. Coverage of the study universe went from 71% to 88%, and
2006, the worst year, improved the most: 48% to 69%.

**What's left:** 1,024 rows across 223 tickers, down from 2,946. Most of
the remainder is genuine ticker reuse — "DD" was E.I. du Pont before 2017
and an unrelated DuPont spinoff after 2019 — which is exactly the kind of
thing that has to be settled with evidence rather than convenience. The
worklist ranks them by how much each one would recover: the top 50 would
close nearly half of what's left.

One practical note: a two-hour run died partway through because the
network dropped for a minute. Beyond making it wait that out, the more
important fix was that a failed search used to be recorded identically to
a search that ran and found nothing — rows that looked investigated when
they never were. Those are now labelled honestly and retried.

## 2026-08-16 (later) — We were counting some things we shouldn't have been

The goal for this round was small: 86 company-years where we'd found the
annual report and downloaded it fine, but couldn't pull the risk-factors
section out of it. We recovered 28. We also found 99 entries that were
worse than missing, and the honest total came out *lower* than it started:
9,296 risk-factor sections before, 9,219 after.

That drop is the point, so it's worth being clear about.

**First, the part that didn't go as predicted.** I'd looked at one company
(Bank of New York Mellon), worked out exactly why its filings failed, and
estimated the same fix would handle up to 76 of the 86. It handled 14. BNY
Mellon's structure turned out to be BNY Mellon's, not a common pattern. The
rest fail in genuinely different ways — FedEx's section is in the main
document but the heading that follows it is one we don't recognise; Aetna's
is in a separate exhibit with only a single heading to anchor on; Citigroup's
ends at a heading unique to Citigroup.

I tried one more general idea — using each document's own table of contents
to learn what section comes after the risk factors — and it didn't work
either. At that point I stopped, rather than write a special case per
company. Each special case is a new thing that can break, for a handful of
rows, and the way it breaks is by grabbing the *wrong text* silently. A
visible gap is better than that.

**Second, the part that mattered more.** While checking the recovered text
by eye, I looked at the shortest entries in the whole dataset. A typical
risk-factors section runs about 51,000 characters. Ninety-nine of ours were
under 1,500, and 84 were under 600.

They weren't risk factors at all. They were one-sentence pointers saying
where the risk factors actually live — "that information is incorporated
into this report by reference." Wells Fargo's entire twenty-year run was
stored this way, at about 240 characters a year.

This is worse than a missing row. A missing row shows up in the coverage
numbers and you know to be careful. This kind of entry looks like data,
counts as data, and quietly feeds a couple of sentences of legal boilerplate
into a study about how companies write about risk. And it had been inflating
every coverage figure I'd reported all day.

The fix that worked wasn't the obvious one. I first tried matching the
wording, which caught Wells Fargo and U.S. Bancorp but missed Halliburton,
McKesson, Genzyme, Eastman Chemical and AMD — every company phrases it
differently. What generalises is length: nothing that short is a real risk
disclosure in this corpus. So anything under 1,500 characters is now treated
as "we haven't got this one."

The good part is what happens next. Those rows get handed to the retry step,
which goes looking for the document the pointer was pointing at — and often
finds it. Universal Health Services went from six 640-character stubs to six
real sections averaging 90,000 characters. McKesson went from 4 stubs to 17
real sections. Wells Fargo recovered 6 real years and now honestly reports
the other 15 as missing, instead of 20 fake ones.

**Where that leaves things:** 9,219 sections, 87.6% of the study universe
(the 88.3% I reported earlier was overstated by the stubs). The shortest
section in the dataset is now 1,500 characters instead of 200.

To make sure none of this quietly broke what already worked, there's now a
test that re-downloads 300 filings we'd already parsed and checks the text
comes out byte-for-byte identical. It passed — 297 identical, and the only
two differences were stubs being correctly rejected.

## 2026-08-16 (later still) — Putting names to the companies that vanished

The biggest remaining hole was 503 company-years belonging to businesses that
no longer exist — bought, merged, or bankrupted, their ticker symbols long
since recycled or retired. Nobody sells us a lookup table for "who owned this
ticker in 2009," and the commercial one (CRSP, via a university subscription)
wasn't available.

So we did it by proposal and proof. I put forward a company for each of the
111 orphaned tickers from memory, and a script checked every single one
against the government's own filing records before it was allowed anywhere
near the dataset. Nothing gets in on my say-so.

**About a fifth of my proposals were wrong**, and that's the point of the
arrangement. I said ticker TIN was Temple-Inland; the records showed that
company code belongs to something declaring itself Nucor. I said TRB was
Tribune; the filings said otherwise. I gave three different tickers the same
company code out of plain carelessness. Every one of those was caught and
thrown out rather than quietly becoming data.

Two things had to be added along the way, both learned from the first round
of failures. Companies weren't required to print their ticker symbol on the
front page of an annual report until 2019, and plenty didn't — Safeway, Avon,
Legg Mason and US Steel file a decade of reports without the symbol appearing
once. But they say it freely in shareholder proxies. Widening the search to
everything a company filed, rather than just its annual reports, unlocked a
batch of them. Separately, companies in bankruptcy get a "Q" tacked onto their
ticker — Kodak's EK becomes EKDKQ — so those needed matching to the original.

**One near-miss is worth telling.** Three of the confirmations looked plainly
wrong: the ticker for DeVry University came back as a company called Covista,
Washington Mutual as "Maverick Merger Sub 2." I pulled them out before adding
them and checked. They were right — the government lists the company's
*current* name, and those company codes had simply been renamed over the
years. Covista's earlier names include DeVry Education Group. The evidence was
fine; the label was misleading. Now every entry records the former names too,
so anyone reviewing the file later isn't misled the way I nearly was.

**Result:** that category dropped from 503 missing company-years to 175.
Overall we now have risk factors for 9,525 company-years, 90.5% of the target
— up from 7,445 and 71% when the day started. The worst year, 2006, went from
48% to 74%.

**What's left that this approach can't reach:** nineteen companies where the
name matches perfectly but no document they ever filed states their ticker
symbol — Legg Mason, Avon, Bear Stearns, BellSouth among them. A name that
looks right isn't proof, so they stay marked as missing. That's the limit of
what's possible without the commercial crosswalk, and worth revisiting if
university access to it turns up.

## 2026-08-18 — Asking the question the right way round

We were about to hand-build a lookup table of which company owned which
ticker symbol in which year — the thing a commercial data subscription would
sell us. Before starting, I checked whether our automated approach had really
run out of road. It hadn't. We'd just been asking the wrong question.

Up to now, to confirm that a company owned a ticker, we searched that
company's filings for the ticker itself. That works for something
distinctive. It's useless for US Steel, whose ticker is the single letter
"X", or Legg Mason's "LM", or Goodrich's "GR" — those letters appear on
practically every page, so the one document that actually states the symbol
is lost in the noise.

Turning the question around fixed it: instead of "does the letter X appear in
US Steel's filings?", ask "which of US Steel's filings states a ticker
symbol, and what does it say?" The answer comes back "X". Same standard of
proof — the company's own document, saying its own symbol — just a question
it can actually answer.

Twelve of the thirteen companies that had been stuck were resolved
immediately. Four others had previously come back with the *wrong* answer,
which was more troubling: the old search wasn't just failing, it was
surfacing misleading documents. Then the same technique was pointed at the
larger group — companies that were bought or renamed partway through, like
Xerox, DuPont, Cigna, Time Warner, Medtronic — and confirmed 49 out of 50.

**So, on the manual table: it was doable — about 160 companies, half a day —
but it was the wrong tool.** Slower, harder to check, and it depends on the
least reliable part of this whole process, which is me. Across seven rounds
of proposals, about one in five of my guesses was wrong. I said one ticker
belonged to Hospira; the records said Bimini Capital. I said another was
Monster Worldwide; it was Lamar Advertising. Every one of those was caught by
the checking step rather than by my own confidence. That's precisely why
guessing is allowed here at all — nothing gets in on a guess.

**Where we've ended up:** risk factors for 10,102 company-years, 96% of the
target. This morning it was 71%. The year that was worst, 2006, went from 48%
to 85%.

The identification problem — figuring out which company a ticker belonged to
— is essentially finished: 84 unresolved rows, down from 2,946. What's left
is mostly the other problem, pulling the text out of unusually structured
documents, which we deliberately stopped chasing because the way it fails is
by quietly grabbing the wrong text.

## 2026-08-18 — Auditing our own work, and finding Merck wasn't Merck

Went looking for what we'd got wrong. Found three companies attributed to the
wrong filings, and one bug in the checking code I'd written earlier the same
day. The total went down slightly as a result — from 10,102 risk-factor
sections to 10,088 — which is the right direction when the thing you removed
was wrong.

**Merck.** For 2006 through 2009 we had been storing Schering-Plough's risk
factors under Merck's name. The reason is a genuinely confusing piece of
corporate history: when the two merged in 2009, Schering-Plough was
technically the buyer, renamed itself Merck, and kept its own filing account.
So the account that says "Merck" today was filing as Schering-Plough back
then. Our check confirmed that the account had filed an annual report that
year — true — but not that it was Merck at the time.

The way we found it was simple and worth keeping: look for cases where two
different ticker symbols point at the same company account in the same year.
Usually that's harmless — Google has two share classes, GOOG and GOOGL, both
filed together. But those always look alike. "MRK and SGP" don't, and neither
do "CTAS and SRCL" or "MDLZ and KRFT". Three real errors, found by one cheap
question.

**The bug in my own checking code** is the one worth dwelling on. Earlier I
built a check that reads a filing and works out which ticker symbol the
company claims for itself — the thing that stopped us attributing Teradyne's
data to Trump Entertainment. It had a hole. Cintas's annual report mentions
selling a business to "Stericycle, Inc. (Nasdaq: SRCL)" — naming *another*
company and its symbol. My code read that as Cintas claiming SRCL, and
Stericycle's 2015 row was duly filed under Cintas.

The distinction I'd missed is that a company writes its own symbol one way
("trades under the symbol X") and other companies' symbols another way
("(Nasdaq: X)"). The second form now only counts if it appears on the cover
page, where no other company gets mentioned.

That also meant throwing away 7,652 cached results computed with the faulty
rule, which is why this took a while to redo.

**The lasting part** is a new checking step that runs every time the pipeline
does, testing five things that would otherwise fail silently — including the
two-tickers-one-company test that caught all of this. It passes now, and it
will fail loudly if anything like this comes back.

**The general lesson**, which is now written into the technical notes: every
mistake we've found in this dataset has been something that *looked* like
data but wasn't — a placeholder counted as a risk disclosure, one company's
report counted as another's, a network failure counted as a real answer. None
of them show up as a lower number. They all show up as a higher one. So the
figures that drop after an audit are the ones worth trusting.

## 2026-08-23 — The chart was drawn from a table that had gone stale

Set out to close the remaining gaps in the dataset. Found something else
first, and it matters more.

The table feeding the tariffs chart was built on 3 August. The underlying
collection of filings was rebuilt on 21 August — the day we took coverage
from 71% of companies to 96%. For eighteen days the chart had been drawn
from the old table, and nobody could tell, because a stale table looks
exactly like a fresh one.

The damage wasn't spread evenly. The August work recovered old filings much
more than recent ones, so the stale chart understated 2006 by about half
while barely touching 2025. **That is what produced the headline claim** —
"tariff mentions have grown every single year, with not one decrease."
Rebuild the table and the claim dies: mentions fall in 2008, 2015, 2022 and
2024. What's left is still a real and large finding — in 2006 about a
quarter of S&P 500 companies named tariffs as a risk, by 2025 it was
six in seven — but it's roughly a tripling, not the near-sextupling the old
numbers implied.

I'd been about to check the growth claim for a completely different reason,
as a robustness check on coverage. It caught this instead.

## 2026-08-23 — Finding the missing filings, and four wrong answers first

Most of the remaining gaps turned out to be one habit, not fifty quirks:
companies that print their risk factors somewhere the usual signposts can't
find them. FedEx's had been sitting in the very document we'd already
downloaded, under a heading that just says "RISK FACTORS" with no item
number. Wells Fargo's and US Bancorp's are in the annual-report attachment.

Writing something to follow them was easy. Getting it to stop in the right
place took five attempts, and every wrong version looked fine from a
distance.

**The first version found 75 sections. About thirty were the wrong text.**
Johnson & Johnson's 2006 entry was three thousand characters of the
Properties section, the Legal Proceedings section, and the one after that.
UPS's was a page of forward-looking-statements boilerplate. Nothing about
the number 75 hinted at any of this; it was the largest number of the five
attempts, and the worst.

So I read them. Then read the next version, and the one after. Each round
caught something the previous guards couldn't see:

- Text that ran straight past the end of the risk factors into the audited
  accounts, ending inside KPMG's opinion.
- A rule that was supposed to catch exactly that, which couldn't see the
  heading "ITEM 7A." at all — a quirk of how the pattern was written.
- A section that stopped mid-sentence, because a phrase used as a signpost
  also appears as an ordinary cross-reference in the text.
- Wells Fargo's 2021 entry, which ended correctly and was missing the first
  eighty thousand characters. It began "(continued)". The only reason to
  doubt it was that every other Wells Fargo year is five times longer.

**One thing I got wrong in a way worth recording.** I'd flagged fourteen
sections as suspect using a keyword search. Two of them were fine — Aetna's
list of risks simply *includes* "a significant failure of internal control
over financial reporting", and my search couldn't tell a risk factor about
auditing from an auditor's report. Reading beats matching.

**Where it ended:** 54 more companies-years of risk factors, 10,088 to
10,142, coverage 96.4%. Seven I refused. Citigroup is among them — its text
is 99% right, with about a page of accounting notes on the end that contain
no tariff mentions and would barely move the chart. Keeping it was tempting
for exactly that reason. But "99% of this column is what the column says"
isn't a standard anything downstream can act on, and this project has turned
that trade down every previous time it came up. So Citi stays missing, and
the way to change my mind is to delete it from a list of seven names, not to
weaken a rule.

The count went 75, 62, 78, 65, 63, 61. The number that was right is the
smallest one.
