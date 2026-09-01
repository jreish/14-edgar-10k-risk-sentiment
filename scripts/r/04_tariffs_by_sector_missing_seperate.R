#!/usr/bin/env Rscript
source(here::here("scripts", "r", "lib_theme.R"))
library(forcats)
library(ggtext)

con <- db_connect()
raw <- dbGetQuery(con, "SELECT year, sector, n_filings FROM tariffs_by_year_sector")

# Coverage gaps = every missing reason EXCEPT not-yet-due. These are permanent
# holes (unresolved company identity, filing never found, Item 1A not
# extracted); not-yet-due is a pending filing, not a coverage failure, so it is
# handled separately as an annotation on the current-year bar.
coverage <- dbGetQuery(con, "
  SELECT year,
         SUM(CASE WHEN reason IS NOT NULL
                   AND COALESCE(sub_reason, '') <> 'not_yet_due'
              THEN 1 ELSE 0 END) AS n_gap,
         SUM(CASE WHEN reason IS NULL THEN 1 ELSE 0 END) AS n_have,
         COUNT(*) AS n_universe
  FROM missing_records
  GROUP BY year
")
pending <- dbGetQuery(con, "
  SELECT year, COUNT(*) AS n_pending
  FROM missing_records
  WHERE reason = 'no_filing_found' AND sub_reason = 'not_yet_due'
  GROUP BY year
")
dbDisconnect(con, shutdown = TRUE)

coverage <- coverage |> mutate(pct_gap = 100 * n_gap / n_universe)

# Share of the filings we actually HAVE that mention tariffs. The bars are
# counts, but the number of filings behind them is not constant -- coverage
# runs from 421 filings in 2006 to ~500 from 2009 on, because early-year
# company identification is harder, not because fewer companies existed. So
# every claim the chart makes is stated as a share and checked against this
# series first; if the two disagreed, the bars would be measuring coverage.
#
# Checked 2026-08-23, and the check earned its keep. The old subtitle claimed
# "not a single year seeing a decrease". Against the rebuilt series the count
# falls in 2008, 2015, 2022, 2024 and (incompletely) 2026, and the share falls
# in six years. The claim was never a fact about disclosure -- it was an
# artifact of a tariff table that had gone 20 days stale, built on the corpus
# as it stood before the CIK-resolution work took coverage from 71% to 96%.
# Because that work recovered early years hardest, the stale series understated
# 2006 by roughly half and manufactured a monotone rise. Hence a magnitude in
# the subtitle, not a streak.
mention_rate <- raw |>
  group_by(year) |>
  summarise(mentions = sum(n_filings), .groups = "drop") |>
  inner_join(coverage |> select(year, n_have), by = "year") |>
  mutate(pct = 100 * mentions / n_have)

trends <- top_n_plus_other(raw, "sector", "n_filings", n = 7) |>
  group_by(year, sector) |>
  summarise(n_filings = sum(n_filings), .groups = "drop")

sector_totals <- trends |> group_by(sector) |> summarise(total = sum(n_filings)) |> arrange(desc(total))
top7 <- setdiff(sector_totals$sector, "Other")
sector_order <- c(top7, "Other")
named_colors <- unname(okabe_ito[setdiff(names(okabe_ito), "black")])[seq_along(top7)]
sector_colors <- setNames(c(named_colors, "grey65"), sector_order)
trends <- trends |> mutate(sector = factor(sector, levels = sector_order))

labels_end <- trends |>
  filter(year == max(year)) |>
  arrange(sector) |>
  mutate(
    ymax = cumsum(n_filings), ymin = lag(ymax, default = 0), ymid = (ymin + ymax) / 2
  )

bar_totals <- trends |> group_by(year) |> summarise(bar_top = sum(n_filings), .groups = "drop")

leader_gap <- 0.12
base_label_dx <- 1.2 - leader_gap

label_x_nudge <- c(
  "Industrials" = 0, "Information Technology" = 0, "Consumer Discretionary" = 0,
  "Consumer Staples" = 0, "Health Care" = 0, "Materials" = 0, "Utilities" = 0,
  "Other" = 0
)

label_y_nudge <- c(
  "Industrials" = 0, "Information Technology" = -15, "Consumer Discretionary" = 0,
  "Consumer Staples" = -5, "Health Care" = -15, "Materials" = 0, "Utilities" = 0,
  "Other" = 0
)

label_data <- labels_end |>
  mutate(sector = as.character(sector)) |>
  select(year, sector, ymid) |>
  mutate(
    sector = factor(sector, levels = sector_order),
    leader_x = year + 0.4 + leader_gap,
    label_x = leader_x + base_label_dx + label_x_nudge[as.character(sector)],
    label_y = ymid + label_y_nudge[as.character(sector)]
  )

y_top <- max(bar_totals$bar_top)

# Annotation anchors, read off the bars they point at. bar_at() keeps the
# arithmetic in one place so a rebuilt tariff series moves the annotations
# with it instead of stranding them mid-bar.
bar_at <- function(y) bar_totals$bar_top[bar_totals$year == y]
t1_low <- bar_at(2016) + y_top * 0.05
t1_high <- max(sapply(2016:2020, bar_at)) + y_top * 0.06
t2_low <- bar_at(2024) + y_top * 0.02
t2_high <- t1_high + y_top * 0.16

# Drop the Trump 1.0 elbow and its label 15 units on the y. This happens after
# t2_high is set, so Trump 2.0 -- whose top is pinned to t1_high above -- does
# not move with it.
t1_low <- t1_low - 15
t1_high <- t1_high - 15

title_x <- min(trends$year)
title_y <- y_top * 1.3
subtitle_gap <- y_top * 0.1

# The latest year is incomplete, and an unmarked short bar is worse than no
# bar: as drawn without this, 2026 sits BELOW 2025 and reads as the first
# decline in the series, when the only thing that fell is how many companies
# have filed so far. Worse, the shortfall is not spread evenly -- 21 of the 49
# pending filers are Information Technology -- so the shape of the top bar is
# misleading too, not just its height.
#
# So the bar stays solid at what has actually been counted and carries a
# hatched cap for what is still to come, sized at the pending filers times
# this year's observed mention rate. That number is a projection and is
# labelled as one. The alternative -- hatching the whole bar -- tells the
# reader to distrust the year without telling them which way it is wrong, and
# here we know which way: up. (The prior build sized this box at one unit per
# missing company, which silently assumed every pending filer will mention
# tariffs; at the current rate that overstates it by about a fifth.)
latest_year <- max(trends$year)
n_pending <- pending$n_pending[pending$year == latest_year]
latest_rate <- mention_rate$pct[mention_rate$year == latest_year] / 100
pending_top <- bar_totals$bar_top[bar_totals$year == latest_year]
projected <- n_pending * latest_rate

pending_box <- data.frame(
  xmin = latest_year - 0.4, xmax = latest_year + 0.4,
  ymin = pending_top, ymax = pending_top + projected
)
pending_hatch <- make_hatch(
  pending_box$xmin, pending_box$xmax, pending_box$ymin, pending_box$ymax
)
pending_note <- data.frame(
  x = latest_year + 0.55,
  y = pending_box$ymax,
  label = sprintf("~%.0f more expected:\n%d companies have\nnot yet filed for %d",
                  projected, n_pending, latest_year)
)

# --- Small data frames for the title/subtitle richtext layers ---
title_df <- data.frame(
  x = title_x, y = title_y + 10,
  label = "<b>Risky Business</b>"
)
# The claim is a magnitude, not a streak. "Not a single year saw a decrease"
# was a property of the counting rather than of the world: it needs a footnote
# the moment coverage changes, it is contradicted on the page by the pending
# 2026 bar, and against the rebuilt series it is false outright in six years.
# Stating the share instead makes the robustness check the headline
# rather than a defensive footnote -- and it is the bigger number anyway.
# Built from the data so it cannot go stale when the pipeline is re-run.
rate_first <- mention_rate$pct[mention_rate$year == min(mention_rate$year)]
rate_last_complete <- mention_rate$pct[mention_rate$year == latest_year - 1]
subtitle_df <- data.frame(
  x = title_x + .1,
  y = title_y - subtitle_gap - 30,
  label = sprintf(
    paste0("***In 2006, %.0f%% of S&amp;P 500 annual reports named<br>",
           "tariffs as a risk factor. By %d it was %.0f%%.***"),
    rate_first, latest_year - 1, rate_last_complete
  )
)

x_scale <- scale_x_continuous(
  breaks = seq(2006, 2026, by = 2),
  # Limits must span the direct-label column at x~2027.6, or the sector labels
  # are dropped as out-of-range. Both figures reuse the same scale so, side by
  # side, the year axes still line up even though they are now separate images.
  limits = c(2005.5, 2028),
  expand = expansion(mult = c(0.01, 0.01))
)

p <- ggplot(trends, aes(x = year, y = n_filings, fill = sector)) +
  geom_col(position = position_stack(reverse = TRUE), width = 0.8, color = "black", linewidth = 0.3) +
  geom_segment(
    data = label_data, aes(x = leader_x, y = label_y, xend = label_x, yend = label_y, color = sector),
    inherit.aes = FALSE, linewidth = 0.4
  ) +
  geom_text(
    data = label_data, aes(x = label_x, y = label_y, label = sector, color = sector),
    inherit.aes = FALSE, hjust = 0, fontface = "bold", size = 4.5, family = "Source Sans 3"
  ) +
  # ---- Hatched cap: what the latest (incomplete) year is still owed ----
geom_rect(
  data = pending_box, aes(xmin = xmin, xmax = xmax, ymin = ymin, ymax = ymax),
  inherit.aes = FALSE, fill = NA, colour = "grey35", linewidth = 0.3
) +
  geom_segment(
    data = pending_hatch, aes(x = x, xend = xend, y = y, yend = yend),
    inherit.aes = FALSE, colour = "grey55", linewidth = 0.25
  ) +
  geom_text(
    data = pending_note, aes(x = x, y = y, label = label),
    inherit.aes = FALSE, hjust = 0, vjust = 1, lineheight = 0.95,
    size = 3.4, colour = "grey35", fontface = "italic", family = "Source Sans 3"
  ) +
  # ---- Title: crisp black text over an opaque white box ----
geom_richtext(
  data = title_df, aes(x = x, y = y, label = label),
  inherit.aes = FALSE, hjust = 0, vjust = 1,
  size = 15, colour = "black", family = "Source Sans 3",
  fill = "white",     # opaque backing to cover the bars/lines
  label.color = NA    # no border box
) +
  # ---- Subtitle: crisp grey text over an opaque white box ----
geom_richtext(
  data = subtitle_df, aes(x = x, y = y, label = label),
  inherit.aes = FALSE, hjust = 0, vjust = 1,
  size = 5, colour = "grey50", family = "Source Sans 3",
  fill = "white",
  label.color = NA
) +
  scale_fill_manual(values = sector_colors) +
  scale_color_manual(values = sector_colors) +
  x_scale +
  scale_y_continuous(
    breaks = seq(0, 500, by = 100),
    expand = expansion(mult = c(0, 0.01))
  ) +
  
  # ---- Elbow connector: one vertical line + one horizontal line ----
# Both annotations keep the years the author chose -- 2016 for Trump 1.0,
# 2024 for Trump 2.0 -- but their heights are now read off the bars instead
# of hardcoded. They were pinned at y = 145 and y = 330 against a tariff
# series that has since been rebuilt (the table feeding this chart was 20
# days stale), and at the current heights both anchor dots landed INSIDE
# their bars rather than just above them.
annotate("segment",
         x = 2016, xend = 2016, y = t1_low, yend = t1_high,
         linewidth = 0.6) +
  # horizontal leg: across to the upper anchor (the label rests on this line)
  annotate("segment",
           x = 2016, xend = 2020, y = t1_high, yend = t1_high,
           linewidth = 0.6) +
  # dot at the lower anchor
  annotate("point", x = 2016, y = t1_low,
           shape = 16, size = 1, stroke = 1, color = "black") +
  # dot at the upper anchor
  annotate("point", x = 2020, y = t1_high,
           shape = 16, size = 1, stroke = 1, color = "black") +
  # "Trump 1.0" sitting on top of the horizontal line
  annotate("label", x = 2018, y = t1_high + y_top * 0.008, label = "Trump 1.0",
           vjust = 0, fill = "white", label.size = 0,
           fontface = "bold", size = 4.5, family = "Source Sans 3",
           color = "black") +
  
  # ---- Dotted Elbow connector: one vertical line ----
annotate("segment",
         x = 2024, xend = 2024, y = t2_low, yend = t2_high,
         linewidth = 0.6,
         linetype = "dotted") +
  # dot at the lower anchor
  annotate("point", x = 2024, y = t2_low,
           shape = 16, size = 1, stroke = 1, color = "black") +
  
  # "Trump 2.0" sitting on top of the vertical dotted line
  annotate("label", x = 2024, y = t2_high + y_top * 0.008, label = "Trump 2.0",
           vjust = 0, fill = "white", label.size = 0,
           fontface = "bold", size = 4.5, family = "Source Sans 3",
           color = "black") +
  
  coord_cartesian(clip = "off") +
  
  # Standalone now, so the main plot carries its own x-axis labels (they used
  # to be blanked here because the coverage strip below was showing them), and
  # the bottom margin is restored from 0 to give those labels room.
  theme(
    plot.margin = margin(t = 5.5, r = 150, b = 5.5, l = 5.5)
  ) +
  labs(
    x = NULL, y = "Filings mentioning tariffs",
    caption = paste(
      SOURCE_CAPTION_BASE,
      "Top 7 sectors by total tariff mentions shown individually; the rest are grouped as \"Other\".",
      sprintf(paste("Bars are counts. Coverage is not constant (%d filings in %d, %d in %d), so the series was also checked as a",
                    "share of\nfilings held: it rises from %.0f%% to %.0f%% on the same shape. Hatched cap on %d = pending filers x %d's",
                    "observed rate, a projection."),
              coverage$n_have[coverage$year == min(coverage$year)], min(coverage$year),
              coverage$n_have[coverage$year == latest_year - 1], latest_year - 1,
              rate_first, rate_last_complete, latest_year, latest_year),
      sep = "\n"
    )
  )

# ---- Coverage strip: share of the universe with no usable filing, every year ----
strip <- ggplot(coverage, aes(x = year, y = pct_gap)) +
  geom_col(width = 0.8, fill = "grey55") +
  annotate("text",
           x = 2026.4, y = max(coverage$pct_gap) * 0.98,
           label = "No usable risk-factor section (% of universe)",
           hjust = 1, vjust = 1, size = 3.4, colour = "grey35",
           fontface = "bold", family = "Source Sans 3") +
  x_scale +
  scale_y_continuous(
    breaks = c(0, 5, 10, 15),
    labels = function(v) paste0(v, "%"),
    expand = expansion(mult = c(0, 0.08))
  ) +
  coord_cartesian(clip = "off") +
  # Top margin restored from 0 to 5.5 now that this stands alone and needs
  # clearance above the panel for its own title.
  theme(
    plot.margin = margin(t = 5.5, r = 150, b = 5.5, l = 5.5),
    panel.grid.major.y = element_line(linewidth = 0.25),
    axis.title.y = element_blank()
  ) +
  labs(
    title = "Claude's 10-K Coverage Varies by Year",
    x = NULL,
    caption = paste(
      SOURCE_CAPTION_BASE,
      "Share of the S&P 500 universe with no usable risk-factor section that year -- unresolved company",
      "identity, filing never found, or no section extractable. Filings not yet due are excluded: pending, not missing.",
      "Counted as gaps but recorded separately: filers who answered Item 1A by reference -- pointing to an exhibit, or",
      "to a safe-harbour cautionary statement in place of risk factors. They disclosed something; it is not the same",
      "object as the sections counted here, so it is not counted (sub_reason no_item_1a_incorporated in missing_records).",
      sep = "\n"
    )
  )

# ---- Write the two figures separately ----
fig_dir <- here::here("output", "figures")
dir.create(fig_dir, showWarnings = FALSE, recursive = TRUE)

# Graph 1: the stacked sector chart on its own.
main_path <- file.path(fig_dir, "tariffs_by_sector.png")
ggsave(main_path, p, width = 11, height = 7, dpi = 300, bg = "white")
message("Wrote ", main_path)

# Graph 2: the coverage strip on its own.
coverage_path <- file.path(fig_dir, "tariffs_coverage_by_year.png")
ggsave(coverage_path, strip, width = 11, height = 4.0, dpi = 300, bg = "white")
message("Wrote ", coverage_path)