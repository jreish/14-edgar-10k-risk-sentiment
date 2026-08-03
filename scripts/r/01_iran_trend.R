#!/usr/bin/env Rscript
# Iran-related keyword mentions in S&P 500 risk factors, 2006-2026.
# Input: topic_trends_by_year (DuckDB). Output: output/figures/iran_trend.png

source(here::here("scripts", "r", "lib_theme.R"))

con <- db_connect()
trend <- dbGetQuery(con, "SELECT year, n_filings FROM topic_trends_by_year WHERE topic = 'iran' ORDER BY year")
dbDisconnect(con, shutdown = TRUE)

conflict_year <- 2025

p <- ggplot(trend, aes(x = year, y = n_filings)) +
  annotate("rect", xmin = conflict_year - 0.5, xmax = conflict_year + 0.5,
           ymin = -Inf, ymax = Inf, fill = okabe_ito["vermillion"], alpha = 0.12) +
  geom_line(color = okabe_ito["blue"], linewidth = 1) +
  geom_point(color = okabe_ito["blue"], size = 2) +
  annotate("text", x = conflict_year, y = max(trend$n_filings) * 1.05,
           label = "2025 Iran-Israel-US\nconflict", hjust = 0.5, vjust = 0,
           size = 3.4, color = "grey30", lineheight = 0.9) +
  scale_x_continuous(breaks = seq(2006, 2026, by = 2)) +
  scale_y_continuous(expand = expansion(mult = c(0.02, 0.15))) +
  labs(
    title = "Iran-related risk disclosure has risen gradually, not sharply, since 2006",
    subtitle = "S&P 500 10-K filings per year whose Item 1A mentions Iran, Hormuz, Israel, the Middle East, or an energy crisis",
    x = NULL, y = "Filings mentioning Iran",
    caption = paste(
      SOURCE_CAPTION_BASE,
      "The rise looks like a multi-year trend toward broader sanctions/geopolitical risk disclosure",
      "rather than a single event-driven spike; 2026 (the first filing year to substantially cover FY2025,",
      "when the conflict occurred) shows the highest count on record.",
      sep = "\n"
    )
  )

out_path <- here::here("output", "figures", "iran_trend.png")
dir.create(dirname(out_path), showWarnings = FALSE, recursive = TRUE)
ggsave(out_path, p, width = 10, height = 6, dpi = 300, bg = "white")
message("Wrote ", out_path)
