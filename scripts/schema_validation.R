#!/usr/bin/env Rscript
# =============================================================================
# schema_validation.R
#
# Validate a curated metadata table against a MetaHarmonizer schema table
# (e.g. schema/cbioportal/cbioportal_schema.csv). The schema CSV is the single
# source of truth produced in intro.qmd; this script turns it into a validation
# schema and checks a data file against it, reporting:
#
#   * required fields that are missing                       (error)
#   * columns not covered by the schema                      (error)
#   * values not coercible to the declared col.class         (warning)
#   * values outside a static/custom enum's allowed set      (warning)
#   * values not matching the declared regex pattern         (warning)
#
# The heavy lifting lives in OmicsMLRepoCuration (tableToYamlSchema +
# validateDataAgainstSchema); this script is just the CLI around them, mirroring
# build_schema_artifacts.R. A companion schema_validation.py reproduces the same
# checks in Python.
#
# Usage:
#   Rscript scripts/schema_validation.R \
#       --schema schema/cbioportal/cbioportal_schema.csv \
#       --data   my_records.csv \
#       [--strict]          # treat warnings as failures too
#
# Exit status: 0 if valid, 1 if errors (or, with --strict, any warnings), 2 on
# a usage/IO problem. --data may be omitted to run a built-in self-check on the
# schema itself.
# =============================================================================

suppressWarnings(suppressMessages({
  library(OmicsMLRepoCuration)   # tableToYamlSchema(), validateDataAgainstSchema()
}))

# ---- tiny arg parser --------------------------------------------------------
parse_args <- function(args) {
  opts <- list(
    schema    = "schema/cbioportal/cbioportal_schema.csv",
    data      = NULL,             # required (a CSV of records to validate)
    wildcards = c("Not applicable", "Not reported"),
    strict    = FALSE
  )
  i <- 1
  while (i <= length(args)) {
    a <- args[[i]]
    take <- function() { i <<- i + 1; args[[i]] }
    switch(a,
      "--schema"    = { opts$schema    <- take() },
      "--data"      = { opts$data      <- take() },
      "--wildcards" = { opts$wildcards <- trimws(strsplit(take(), ",")[[1]]) },
      "--strict"    = { opts$strict    <- TRUE },
      stop("Unknown argument: ", a)
    )
    i <- i + 1
  }
  opts
}

# ---- readers ----------------------------------------------------------------
# Schema tables are comma-separated CSV (write.csv()). Files still in the
# legacy write.table() layout (space-separated, quoted) are detected from the
# header line and read with read.table(). check.names=FALSE keeps the dotted
# column names (col.name, col.class, ...) intact.
read_schema_table <- function(path) {
  header <- readLines(path, n = 1L, warn = FALSE)
  if (grepl('^\\s*"?[^",]*"?\\s*,', header)) {
    read.csv(path, stringsAsFactors = FALSE, na.strings = "NA",
             check.names = FALSE)
  } else {
    read.table(path, header = TRUE, stringsAsFactors = FALSE,
               na.strings = "NA", check.names = FALSE)
  }
}

# Data files are ordinary comma CSVs; check.names=FALSE preserves field names
# verbatim (e.g. ASCN.CCF_EXPECTED_COPIES) so they line up with the schema.
read_data <- function(path) {
  read.csv(path, stringsAsFactors = FALSE, check.names = FALSE,
           na.strings = c("NA", ""))
}

# ---- reporting --------------------------------------------------------------
print_report <- function(res, data) {
  message("=== Validation report ===")
  message("Rows: ", nrow(data), "   Columns: ", ncol(data))
  message("Overall valid: ", res$valid)
  message("Errors: ", length(res$errors), "   Warnings: ", length(res$warnings))
  if (length(res$errors)) {
    message("\nErrors:")
    for (e in res$errors) message("  • ", e)
  }
  if (length(res$warnings)) {
    message("\nWarnings:")
    for (w in res$warnings) message("  • ", w)
  }
  if (res$valid && !length(res$warnings)) message("\n✓ All checks passed.")
}

# ---- main -------------------------------------------------------------------
main <- function() {
  opts <- parse_args(commandArgs(trailingOnly = TRUE))

  if (!file.exists(opts$schema)) {
    message("Schema file not found: ", opts$schema); quit(status = 2)
  }
  message("Reading schema: ", opts$schema)
  schema_table <- read_schema_table(opts$schema)
  # Name the schema after the file's prefix (cbioportal_schema.csv -> cbioportal).
  schema_name <- sub("(_schema)?\\.csv$", "", basename(opts$schema))
  schema <- tableToYamlSchema(schema_table, schema_name = schema_name)
  message("Schema fields: ", length(schema) - 1,
          "   Required: ",
          paste(getRequiredFields(schema), collapse = ", "))

  if (is.null(opts$data)) {
    # Self-check: a schema with no data still validates its own required set is
    # internally consistent. Build a one-row frame of just the required fields.
    message("\nNo --data supplied; running a self-check on required fields.\n")
    req <- getRequiredFields(schema)
    data <- as.data.frame(
      setNames(as.list(rep("x", length(req))), req),
      stringsAsFactors = FALSE, check.names = FALSE
    )
  } else {
    if (!file.exists(opts$data)) {
      message("Data file not found: ", opts$data); quit(status = 2)
    }
    message("Reading data:   ", opts$data)
    data <- read_data(opts$data)
  }

  res <- validateDataAgainstSchema(data, schema, wildcard_values = opts$wildcards)
  print_report(res, data)

  failed <- !res$valid || (opts$strict && length(res$warnings) > 0)
  quit(status = if (failed) 1L else 0L)
}

if (identical(environment(), globalenv())) main()
