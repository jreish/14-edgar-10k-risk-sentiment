# Interactive exploration. Source this in RStudio, then poke at the data.
#
#   source(here::here("scripts", "r", "00_explore.R"))
#
# Unlike the other scripts in scripts/r/, this one renders nothing. It loads
# the finished tables into the global environment and defines a handful of
# helpers for answering the questions that keep coming up: what does a given
# company's risk section actually say, why is a particular row missing, and
# whether a result survives dropping the rows that were recovered rather than
# extracted normally.
#
# That last one matters more than it sounds. `source_location` records WHERE
# each stored section was printed, and the three non-standard kinds are not
# evenly spread -- exhibit-incorporated filings skew to banks and to early
# years, and carried-forward rows repeat a document across two years. A
# word-count or sentiment series that treats all 10,164 rows as independent
# observations is double-counting the last group and mixing genres in the
# others. `only_inline()` is the one-word way to check whether a finding
# depends on them.

source(here::here("scripts", "r", "lib_theme.R"))

con <- db_connect()

sections   <- dbGetQuery(con, "SELECT * FROM risk_factors_index")
missing    <- dbGetQuery(con, "SELECT * FROM missing_records")
universe   <- dbGetQuery(con, "SELECT * FROM sp500_universe_raw")
sectors    <- dbGetQuery(con, "SELECT * FROM company_sectors")
tariffs    <- dbGetQuery(con, "SELECT * FROM tariffs_by_year_sector")
topics     <- dbGetQuery(con, "SELECT * FROM topic_trends_by_year")
keywords   <- dbGetQuery(con, "SELECT * FROM keyword_hits")
late       <- dbGetQuery(con, "SELECT * FROM late_filings_not_ingested")
meta       <- dbGetQuery(con, "SELECT * FROM _meta")

dbDisconnect(con, shutdown = TRUE)


# --- reading the actual text ------------------------------------------------

# The point of the whole pipeline is the prose, and it is easy to go a long
# way without ever looking at it. Every defect this project has found was
# found by reading a section, not by inspecting a count.
rf <- function(ticker, year, chars = 2000) {
  row <- sections[sections$ticker == ticker & sections$year == year, ]
  if (nrow(row) == 0) return(why_missing(ticker, year))
  if (!isTRUE(row$has_item_1a[1])) return(why_missing(ticker, year))

  # encoding matters here: filings are full of typographic quotes and dashes,
  # and reading them in the native encoding turns "Management's" into mojibake
  # -- which is also how a curly apostrophe silently defeated a parser rule
  # earlier in this project.
  path <- here::here(row$file_path[1])
  text <- paste(readLines(path, warn = FALSE, encoding = "UTF-8"), collapse = "\n")
  cat(sprintf("%s %d  |  %s  |  filed %s  |  %s chars\n",
              ticker, year, row$source_location[1], row$filing_date[1],
              format(row$char_count[1], big.mark = ",")))
  cat(strrep("-", 70), "\n")
  cat(substr(text, 1, chars))
  if (nchar(text) > chars) cat(sprintf("\n\n[... %s more characters]\n",
                                       format(nchar(text) - chars, big.mark = ",")))
  invisible(text)
}

# Head and tail together, which is how you catch a section that starts in the
# right place and runs past its end -- the failure that a length alone hides.
rf_ends <- function(ticker, year, chars = 300) {
  text <- rf(ticker, year, chars = chars)
  if (is.character(text)) {
    cat("\n... TAIL ...\n")
    cat(substr(text, max(1, nchar(text) - chars), nchar(text)), "\n")
  }
  invisible(text)
}


# --- why is this row not here? ----------------------------------------------

why_missing <- function(ticker, year) {
  row <- missing[missing$ticker == ticker & missing$year == year, ]
  if (nrow(row) == 0) {
    cat(sprintf("%s %d is not in the S&P 500 universe for that year.\n", ticker, year))
    return(invisible(NULL))
  }
  cat(sprintf("%s %d: %s%s\n", ticker, year,
              ifelse(is.na(row$reason[1]), "present", row$reason[1]),
              ifelse(is.na(row$sub_reason[1]), "", paste0(" / ", row$sub_reason[1]))))
  invisible(row)
}


# --- the shape of what is missing -------------------------------------------

coverage <- function() {
  missing |>
    group_by(year) |>
    summarise(
      universe = n(),
      have     = sum(is.na(reason)),
      pending  = sum(!is.na(sub_reason) & sub_reason == "not_yet_due"),
      gaps     = universe - have - pending,
      pct_have = round(100 * have / universe, 1),
      .groups  = "drop"
    )
}

gaps <- function(year = NULL, reason = NULL) {
  out <- missing[!is.na(missing$reason), ]
  if (!is.null(year))   out <- out[out$year == year, ]
  if (!is.null(reason)) out <- out[out$reason == reason | (!is.na(out$sub_reason) &
                                                             out$sub_reason == reason), ]
  out[order(out$ticker, out$year), c("year", "ticker", "cik", "reason", "sub_reason")]
}

reasons <- function() {
  missing |>
    filter(!is.na(reason)) |>
    count(reason, sub_reason, sort = TRUE)
}


# --- provenance -------------------------------------------------------------

# Drop everything that was not extracted from an Item 1A heading in the
# filing's own primary document. Use it to re-run any finding and see whether
# it holds on the conservative subset.
only_inline <- function(df = sections) {
  df[df$has_item_1a & df$source_location == "item_1a_inline", ]
}

provenance <- function() {
  sections |>
    filter(has_item_1a) |>
    count(source_location, sort = TRUE) |>
    mutate(median_chars = sapply(source_location, function(s)
      median(sections$char_count[sections$has_item_1a &
                                   sections$source_location == s], na.rm = TRUE)))
}

# Section length by year, both ways. If these two lines diverge, the growth in
# risk-factor length is partly a story about which documents got recovered
# rather than about how companies write.
length_by_year <- function() {
  all_rows <- sections |> filter(has_item_1a) |> group_by(year) |>
    summarise(all = median(char_count), n_all = n(), .groups = "drop")
  inline <- only_inline() |> group_by(year) |>
    summarise(inline_only = median(char_count), n_inline = n(), .groups = "drop")
  left_join(all_rows, inline, by = "year")
}


cat("Loaded:", nrow(sections), "filing rows,", sum(sections$has_item_1a), "with text;",
    sum(!is.na(missing$reason)), "gaps.\n")
cat("Tables: sections missing universe sectors tariffs topics keywords late meta\n")
cat("Helpers: rf(\"AAPL\", 2020)  rf_ends()  why_missing()  coverage()  gaps()  reasons()\n")
cat("         provenance()  only_inline()  length_by_year()\n")
