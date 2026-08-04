# Shared plotting setup: Okabe-Ito colorblind-safe palette, a restrained
# Kieran-Healy-style theme (minimal chartjunk, direct labeling preferred
# over legends), a DuckDB read-only connector, and a diagonal-hatch helper
# for the missing-data overlay chart.
#
# R is the plotting layer only in this project -- all data processing
# happens in Python (checkpoints 1-7); these scripts just read finished
# tables from the DuckDB database and render them.

library(here)
library(DBI)
library(duckdb)
library(dplyr)
library(ggplot2)

okabe_ito <- c(
  orange         = "#E69F00",
  sky_blue       = "#56B4E9",
  bluish_green   = "#009E73",
  yellow         = "#F0E442",
  blue           = "#0072B2",
  vermillion     = "#D55E00",
  reddish_purple = "#CC79A7",
  black          = "#000000"
)

theme_set(theme_minimal(base_size = 13))
theme_update(
  panel.grid.minor = element_blank(),
  panel.grid.major.x = element_blank(),
  plot.title = element_text(face = "bold", size = rel(1.15)),
  plot.subtitle = element_text(color = "grey30", margin = margin(b = 10)),
  plot.caption = element_text(color = "grey50", size = 9, hjust = 0, margin = margin(t = 10)),
  legend.position = "none"
)

db_connect <- function() {
  dbConnect(duckdb(), here("data", "sp500_10k.duckdb"), read_only = TRUE)
}

# Bin all but the top `n` categories (by total value) into "Other" -- per
# this project's requirement to avoid a dozen unreadable slivers on any
# chart with more than ~7-8 categories.
top_n_plus_other <- function(df, group_col, value_col, n = 7, other_label = "Other") {
  totals <- df |>
    group_by(.data[[group_col]]) |>
    summarise(.total = sum(.data[[value_col]]), .groups = "drop") |>
    arrange(desc(.total))
  top <- totals[[group_col]][seq_len(min(n, nrow(totals)))]
  df |> mutate("{group_col}" := if_else(.data[[group_col]] %in% top, .data[[group_col]], other_label))
}

# Diagonal hatch lines for a missing-data overlay box. Slope is chosen for
# a roughly-45-degree visual appearance on an 11x7in saved panel (physical
# inches-per-data-unit, not a literal 1:1 data-unit ratio -- the x axis
# (years) and y axis (filing counts) have very different scales, so a true
# 1:1 slope would look nearly flat).
make_hatch <- function(xmin, xmax, ymin, ymax, spacing = 0.16, slope = 30) {
  height <- ymax - ymin
  offsets <- seq(xmin - height / slope, xmax, by = spacing)
  segs <- lapply(offsets, function(x0) {
    x_lo <- max(xmin, x0)
    x_hi <- min(xmax, x0 + height / slope)
    if (x_lo >= x_hi) return(NULL)
    data.frame(
      x = x_lo, xend = x_hi,
      y = ymin + slope * (x_lo - x0), yend = ymin + slope * (x_hi - x0)
    )
  })
  bind_rows(segs)
}

SOURCE_CAPTION_BASE <- "Source: SEC EDGAR 10-K filings, S&P 500 constituents (point-in-time historical membership)."
