#!/usr/bin/env Rscript
source(here::here("scripts", "r", "lib_theme.R"))
library(forcats)
library(patchwork)
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
title_x <- min(trends$year)
title_y <- y_top * 1.02
subtitle_gap <- y_top * 0.05

# Pending-filings note, anchored to the latest year's bar. The current year is
# incomplete: its bar can only grow, so the reader must see this before
# reading the last bar as a decline.
latest_year <- max(trends$year)
n_pending <- pending$n_pending[pending$year == latest_year]
pending_top <- bar_totals$bar_top[bar_totals$year == latest_year]
pending_note <- data.frame(
  x = latest_year + 0.6,
  y = y_top * 1.01,
  label = sprintf("%d companies\nhad not yet\nfiled for %d", n_pending, latest_year)
)

# --- Small data frames for the title/subtitle richtext layers ---
title_df <- data.frame(
  x = title_x, y = title_y + 10,
  label = "<b>Risky Business</b>"
)
subtitle_df <- data.frame(
  x = title_x + .1,
  y = title_y - subtitle_gap - 30,
  label = "***S&amp;P 500 10-K filings mentioning tariffs in <br>the Item 1A  Risk Factors section have grown <br>steadily since 2006, with not a single year<br> seeing a decrease.***"
)

x_scale <- scale_x_continuous(
  breaks = seq(2006, 2026, by = 2),
  # Limits must span the direct-label column at x~2027.6, or the sector labels
  # are dropped as out-of-range. Shared by both panels so the bars align.
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
  # ---- Pending-filings note over the latest (incomplete) bar ----
geom_text(
  data = pending_note, aes(x = x, y = y, label = label),
  inherit.aes = FALSE, hjust = 0, vjust = 1, lineheight = 0.95,
  size = 3.6, colour = "grey35", fontface = "italic", family = "Source Sans 3"
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
    expand = expansion(mult = c(0, 0.05))
  ) +

  # ---- Elbow connector: one vertical line + one horizontal line ----
# vertical leg: straight up from the lower anchor
annotate("segment",
         x = 2016, xend = 2016, y = 145, yend = 300,
         linewidth = 0.6) +
  # horizontal leg: across to the upper anchor (the label rests on this line)
  annotate("segment",
           x = 2016, xend = 2020, y = 300, yend = 300,
           linewidth = 0.6) +
  # dot at the lower anchor
  annotate("point", x = 2016, y = 145,
           shape = 16, size = 1, stroke = 1, color = "black") +
  # dot at the upper anchor
  annotate("point", x = 2020, y = 300,
           shape = 16, size = 1, stroke = 1, color = "black") +
  # "Trump 1.0" sitting on top of the horizontal line
  annotate("label", x = 2018, y = 303, label = "Trump 1.0",
           vjust = 0, fill = "white", label.size = 0,
           fontface = "bold", size = 4.5, family = "Source Sans 3",
           color = "black") +

  # ---- Dotted Elbow connector: one vertical line ----
# The riser top and label were pinned just under the old ceiling (416), which
# was set by the not-yet-due box. That box is gone, so the ceiling is now the
# tallest bar; these two values are rescaled to match. The lower dot stays put
# -- it is anchored just above the 2024 bar, not to the ceiling.
annotate("segment",
         x = 2024, xend = 2024, y = 330, yend = 383,
         linewidth = 0.6,
         linetype = "dotted") +
  # dot at the lower anchor
  annotate("point", x = 2024, y = 330,
           shape = 16, size = 1, stroke = 1, color = "black") +

  # "Trump 2.0" sitting on top of the vertical dotted line
  annotate("label", x = 2024, y = 386, label = "Trump 2.0",
           vjust = 0, fill = "white", label.size = 0,
           fontface = "bold", size = 4.5, family = "Source Sans 3",
           color = "black") +

  coord_cartesian(clip = "off") +

  theme(
    plot.margin = margin(t = 5.5, r = 150, b = 0, l = 5.5),
    axis.text.x = element_blank()
  ) +
  labs(x = NULL, y = "Filings mentioning tariffs")

# ---- Coverage strip: share of the universe with no usable filing, every year ----
strip <- ggplot(coverage, aes(x = year, y = pct_gap)) +
  geom_col(width = 0.8, fill = "grey55") +
  annotate("text",
           x = 2026.4, y = max(coverage$pct_gap) * 0.98,
           label = "No usable Item 1A (% of universe)",
           hjust = 1, vjust = 1, size = 3.4, colour = "grey35",
           fontface = "bold", family = "Source Sans 3") +
  x_scale +
  scale_y_continuous(
    breaks = c(0, 5, 10, 15),
    labels = function(v) paste0(v, "%"),
    expand = expansion(mult = c(0, 0.08))
  ) +
  coord_cartesian(clip = "off") +
  theme(
    plot.margin = margin(t = 0, r = 150, b = 5.5, l = 5.5),
    panel.grid.major.y = element_line(linewidth = 0.25),
    axis.title.y = element_blank()
  ) +
  labs(
    x = NULL,
    caption = paste(
      SOURCE_CAPTION_BASE,
      "Top 7 sectors by total tariff mentions shown individually; the rest are grouped as \"Other\".",
      "Lower panel: share of the S&P 500 universe with no usable Item 1A that year -- unresolved company identity,",
      "filing genuinely never found, or Item 1A not extracted. Filings not yet due are excluded: they are pending,",
      "not missing, and are counted separately at top right. All gaps are queryable in missing_records.",
      sep = "\n"
    )
  )

combined <- p / strip + plot_layout(heights = c(4, 1))

out_path <- here::here("output", "figures", "tariffs_by_sector_missing.png")
dir.create(dirname(out_path), showWarnings = FALSE, recursive = TRUE)
ggsave(out_path, combined, width = 11, height = 8.4, dpi = 300, bg = "white")
message("Wrote ", out_path)
