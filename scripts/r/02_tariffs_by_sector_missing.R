#!/usr/bin/env Rscript
source(here::here("scripts", "r", "lib_theme.R"))
library(forcats)
library(patchwork)
library(ggtext)

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
    ymax = cumsum(n_filings), ymin = lag(ymax, default = 0), ymid = (ymin + ymax) / 2
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
  mutate(sector = "Not yet due", ymid = (ymin + ymax) / 2) |>
  select(year, sector, ymid)

leader_gap <- 0.12
base_label_dx <- 1.2 - leader_gap

label_x_nudge <- c(
  "Industrials" = 0, "Information Technology" = 0, "Consumer Discretionary" = 0,
  "Consumer Staples" = 0, "Health Care" = 0, "Materials" = 0, "Utilities" = 0,
  "Other" = 0, "Not yet due" = 0
)

label_y_nudge <- c(
  "Industrials" = 0, "Information Technology" = -15, "Consumer Discretionary" = 0,
  "Consumer Staples" = -5, "Health Care" = -15, "Materials" = 0, "Utilities" = 0,
  "Other" = 0, "Not yet due" = 25
)

label_data <- bind_rows(
  labels_end |> mutate(sector = as.character(sector)) |> select(year, sector, ymid),
  hatch_label
) |>
  mutate(
    sector = factor(sector, levels = c(sector_order, "Not yet due")),
    leader_x = year + 0.4 + leader_gap,
    label_x = leader_x + base_label_dx + label_x_nudge[as.character(sector)],
    label_y = ymid + label_y_nudge[as.character(sector)]
  )

sector_colors_ext <- c(sector_colors, "Not yet due" = unname(okabe_ito["vermillion"]))

y_top <- max(c(bar_totals$bar_top, missing_boxes$ymax))
title_x <- min(trends$year)
title_y <- y_top * 1.02
subtitle_gap <- y_top * 0.05

# --- Small data frames for the title/subtitle richtext layers ---
title_df <- data.frame(
  x = title_x, y = title_y,
  label = "<b>Risky Business</b>"
)
subtitle_df <- data.frame(
  x = title_x, y = title_y - subtitle_gap - 50,
  label = "<i>Tariff mentions in 10-K item 1A have grown<br>steadily since 2006, with large jumps<br>during Trump 1.0 and Trump 2.0</i>"
)

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
  geom_segment(
    data = label_data, aes(x = leader_x, y = label_y, xend = label_x, yend = label_y, color = sector),
    inherit.aes = FALSE, linewidth = 0.4
  ) +
  geom_text(
    data = label_data, aes(x = label_x, y = label_y, label = sector, color = sector),
    inherit.aes = FALSE, hjust = 0, fontface = "bold", size = 4.5, family = "Source Sans 3"
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
  scale_color_manual(values = sector_colors_ext) +
  scale_x_continuous(breaks = seq(2006, 2026, by = 2), expand = expansion(mult = c(0.02, 0.02))) +
  scale_y_continuous(
    expand = expansion(mult = c(0, 0.05))
  ) +
  coord_cartesian(clip = "off") +
  theme(plot.margin = margin(t = 5.5, r = 150, b = 5.5, l = 5.5)) +
  labs(
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