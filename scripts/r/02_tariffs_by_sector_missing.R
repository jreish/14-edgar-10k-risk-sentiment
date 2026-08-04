#!/usr/bin/env Rscript
# Tariff mentions per year, stacked by sector, with a hatched box showing
# ONLY filings that haven't come due yet (sub_reason = 'not_yet_due') --
# not every missing company-year for that sector/year.
#
# This is a deliberate correction of the prior build's version of this
# chart, which hatched every 'no_filing_found' row regardless of reason.
# The hatch box is meant to answer "how much could this year's count still
# grow as pending filings arrive" -- mixing in cik-resolution-stage gaps,
# extraction failures, or genuinely-delinquent filings overstates that
# story and (checked against the prior version) the resolution-stage gaps
# alone run several times the height of the real bars, which would make
# the box unreadable and misleading. See decisions.md.
#
# Every other missing reason is still fully queryable in missing_records --
# this chart just doesn't visualize them in the hatch box.

source(here::here("scripts", "r", "lib_theme.R"))
library(ggrepel)
library(forcats)

con <- db_connect()
raw <- dbGetQuery(con, "SELECT year, sector, n_filings FROM tariffs_by_year_sector")
not_yet_due <- dbGetQuery(con, "
  SELECT year, COUNT(*) AS n_missing
  FROM missing_records
  WHERE reason = 'no_filing_found' AND sub_reason = 'not_yet_due'
  GROUP BY year
")
dbDisconnect(con, shutdown = TRUE)

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
    ymax = cumsum(n_filings), ymin = lag(ymax, default = 0), ymid = (ymin + ymax) / 2,
    segment_color = sector_colors[as.character(sector)]
  )

bar_totals <- trends |> group_by(year) |> summarise(bar_top = sum(n_filings), .groups = "drop")

missing_boxes <- not_yet_due |>
  inner_join(bar_totals, by = "year") |>
  filter(n_missing > 0) |>
  mutate(xmin = year - 0.4, xmax = year + 0.4, ymin = bar_top, ymax = bar_top + n_missing)

hatch_lines <- missing_boxes |>
  rowwise() |>
  reframe(make_hatch(xmin, xmax, ymin, ymax))

hatch_label <- missing_boxes |>
  mutate(
    sector = "Not yet due",
    ymid = (ymin + ymax) / 2,
    segment_color = unname(okabe_ito["vermillion"])
  ) |>
  select(year, sector, ymid, segment_color)

label_data <- bind_rows(
  labels_end |> mutate(sector = as.character(sector)) |> select(year, sector, ymid, segment_color),
  hatch_label
) |>
  mutate(sector = factor(sector, levels = c(sector_order, "Not yet due")))

sector_colors_ext <- c(sector_colors, "Not yet due" = unname(okabe_ito["vermillion"]))

p <- ggplot(trends, aes(x = year, y = n_filings, fill = sector)) +
  geom_col(position = position_stack(reverse = TRUE), width = 0.8, color = "black", linewidth = 0.3) +
  geom_rect(
    data = missing_boxes, aes(xmin = xmin, xmax = xmax, ymin = ymin, ymax = ymax),
    inherit.aes = FALSE, fill = NA, color = "black", linewidth = 0.3
  ) +
  geom_segment(
    data = hatch_lines, aes(x = x, xend = xend, y = y, yend = yend),
    inherit.aes = FALSE, color = okabe_ito["vermillion"], linewidth = 0.5
  ) +
  geom_text_repel(
    data = label_data,
    aes(x = year, y = ymid, label = sector, color = sector, segment.color = segment_color),
    inherit.aes = FALSE, hjust = 0, nudge_x = 1.6, direction = "y",
    min.segment.length = 0, fontface = "bold", size = 3.6, seed = 42
  ) +
  scale_fill_manual(values = sector_colors) +
  scale_color_manual(values = sector_colors_ext) +
  scale_x_continuous(breaks = seq(2006, 2026, by = 2), expand = expansion(mult = c(0.02, 0.16))) +
  scale_y_continuous(expand = expansion(mult = c(0, 0.05))) +
  labs(
    title = "Tariff mentions in S&P 500 risk factors, 2006-2026, by sector",
    subtitle = "10-K filings whose Item 1A mentions tariffs, stacked by sector",
    x = NULL, y = "Filings mentioning tariffs",
    caption = paste(
      SOURCE_CAPTION_BASE,
      "Top 7 sectors by total tariff mentions shown individually; the rest are grouped as \"Other\".",
      "Hatched box = filings not yet due (fiscal year hasn't ended / deadline hasn't passed) --",
      "this year's count could still grow. Filings missing for OTHER reasons (unresolved company",
      "identity, filing genuinely never found, Item 1A not extracted) are excluded from this box --",
      "they are real gaps, not \"still pending,\" and are separately queryable in missing_records.",
      sep = "\n"
    )
  )

out_path <- here::here("output", "figures", "tariffs_by_sector_missing.png")
dir.create(dirname(out_path), showWarnings = FALSE, recursive = TRUE)
ggsave(out_path, p, width = 11, height = 7.6, dpi = 300, bg = "white")
message("Wrote ", out_path)
