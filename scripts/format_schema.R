#!/usr/bin/env Rscript
# Rewrite schema tables in the registry's canonical form.
#
#   Rscript scripts/format_schema.R                 # every schema/*/*_schema*.csv
#   Rscript scripts/format_schema.R PATH [PATH ...]  # just these tables
#   Rscript scripts/format_schema.R --check          # exit 1 if any table would change
#
# Canonical form (see docs/target_schema.md):
#   * UTF-8 comma CSV written by write.csv(row.names = FALSE)
#   * columns in the canonical order; optional columns only when present;
#     unknown columns are kept at the end (with a warning)
#   * empty cells written as NA
#
# Only the layout changes, never a value. Run it after editing a table by hand or
# in a spreadsheet program, so that diffs stay small. Base R only.

CANONICAL <- c("col.name", "col.class", "unique", "required", "scope", "unit.of",
               "propagate.study.value", "multiplevalues", "delimiter", "separator",
               "description", "corpus.type", "allowedvalues", "static.enum",
               "dynamic.enum", "dynamic.enum.property")

format_table <- function(path) {
  df <- utils::read.csv(path, check.names = FALSE, stringsAsFactors = FALSE,
                        na.strings = c("NA", ""), fileEncoding = "UTF-8",
                        encoding = "UTF-8")
  extra <- setdiff(names(df), CANONICAL)
  if (length(extra))
    warning(path, ": non-canonical column(s) kept at the end: ",
            paste(extra, collapse = ", "), call. = FALSE)
  df <- df[, c(intersect(CANONICAL, names(df)), extra), drop = FALSE]
  tmp <- tempfile(fileext = ".csv")
  utils::write.csv(df, tmp, row.names = FALSE, fileEncoding = "UTF-8")
  tmp
}

main <- function(args) {
  check <- "--check" %in% args
  paths <- setdiff(args, "--check")
  if (!length(paths))
    paths <- Sys.glob("schema/*/*_schema*.csv")
  if (!length(paths)) {
    message("no schema tables found (run from the registry root)")
    quit(status = 2)
  }
  changed <- character()
  for (p in paths) {
    if (!file.exists(p)) { message("not found: ", p); quit(status = 2) }
    tmp <- format_table(p)
    same <- identical(readBin(tmp, "raw", file.size(tmp)),
                      readBin(p, "raw", file.size(p)))
    if (!same) {
      changed <- c(changed, p)
      if (!check) file.copy(tmp, p, overwrite = TRUE)
    }
    unlink(tmp)
  }
  verb <- if (check) "needs formatting" else "reformatted"
  for (p in changed) message(verb, ": ", p)
  if (!length(changed)) message("all ", length(paths), " table(s) already canonical")
  if (check && length(changed)) {
    message("run: Rscript scripts/format_schema.R ", paste(changed, collapse = " "))
    quit(status = 1)
  }
}

main(commandArgs(trailingOnly = TRUE))
