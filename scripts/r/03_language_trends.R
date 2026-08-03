#!/usr/bin/env Rscript
# How risk-factor language has shifted over 20 years: small multiples of
# theme-word frequency (per 1,000 words, so length growth doesn't drive the
# trend) plus overall section length growth. Faceting instead of overlaying
# five series in one panel, per this project's small-multiples requirement.

source(here::here("scripts", "r", "lib_theme.R"))
library(tidyr)

con <- db_connect()
raw <- dbGetQuery(con, "SELECT * FROM language_trends_by_year ORDER BY year")
dbDisconnect(con, shutdown = TRUE)

theme_cols <- c(
  "negative_uncertainty_per_1000_words", "cybersecurity_per_1000_words",
  "climate_per_1000_words", "pandemic_per_1000_words", "inflation_supply_chain_per_1000_words"
)
theme_labels <- c(
  negative_uncertainty_per_1000_words = "Negative/uncertainty tone",
  cybersecurity_per_1000_words = "Cybersecurity",
  climate_per_1000_words = "Climate",
  pandemic_per_1000_words = "Pandemic",
  inflation_supply_chain_per_1000_words = "Inflation / supply chain"
)

long <- raw |>
  select(year, all_of(theme_cols)) |>
  pivot_longer(-year, names_to = "theme", values_to = "per_1000") |>
  mutate(theme = factor(theme_labels[theme], levels = unname(theme_labels)))

p_themes <- ggplot(long, aes(x = year, y = per_1000)) +
  geom_line(color = okabe_ito["blue"], linewidth = 0.9) +
  facet_wrap(~theme, scales = "free_y", ncol = 3) +
  scale_x_continuous(breaks = seq(2006, 2026, by = 10)) +
  labs(
    title = "How risk-factor language has shifted, 2006-2026",
    subtitle = "Mentions per 1,000 words of Item 1A text (normalized for the length growth shown separately below)",
    x = NULL, y = "Mentions per 1,000 words",
    caption = paste(
      SOURCE_CAPTION_BASE,
      "Word lists are a small curated set defined directly in the analysis script, not an external",
      "sentiment lexicon -- see decisions.md for the full list and reasoning.",
      sep = "\n"
    )
  ) +
  theme(strip.text = element_text(face = "bold"))

p_length <- ggplot(raw, aes(x = year, y = avg_word_count)) +
  geom_col(fill = okabe_ito["sky_blue"]) +
  scale_x_continuous(breaks = seq(2006, 2026, by = 2)) +
  scale_y_continuous(labels = scales::comma) +
  labs(
    title = "Risk factor sections have roughly tripled in length since 2006",
    x = NULL, y = "Avg. Item 1A word count",
    caption = SOURCE_CAPTION_BASE
  )

out_path <- here::here("output", "figures", "language_trends.png")
dir.create(dirname(out_path), showWarnings = FALSE, recursive = TRUE)
ggsave(out_path, p_themes, width = 11, height = 6.5, dpi = 300, bg = "white")
message("Wrote ", out_path)

out_path2 <- here::here("output", "figures", "word_count_growth.png")
ggsave(out_path2, p_length, width = 10, height = 6, dpi = 300, bg = "white")
message("Wrote ", out_path2)
