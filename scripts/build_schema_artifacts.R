#!/usr/bin/env Rscript
# =============================================================================
# build_schema_artifacts.R
#
# Turn a MetaHarmonizer schema table (e.g. schema/cbioportal/cbioportal_schema.csv)
# into the downstream artifacts consumed by the rest of the ecosystem:
#
#   1. <prefix>.linkml.yaml                       -> LinkML schema
#                                                    (validation + metacurator)
#   2. <prefix>_target_attrs.csv                  -> field_name,is_numeric_field
#                                                    (SchemaMapper target schema)
#   3. <prefix>_target_attrs_allowed_values.json  -> {field: [allowed values]}
#                                                    (SchemaMapper value dict)
#   4. <prefix>_target_attrs_alias_haiku.csv      -> field_name,source,is_numeric_field
#      (+ .meta.json provenance)                     (SchemaMapper alias dict;
#                                                     LLM-generated, needs API key)
#
# Most of the heavy lifting lives in OmicsMLRepoCuration (LinkML conversion) and
# MetaHarmonizerR / MetaHarmonizer (LLM alias generation); this script is just
# the orchestration around them.
#
# Usage:
#   Rscript scripts/build_schema_artifacts.R \
#       --input schema/cbioportal/cbioportal_schema.csv \
#       --outdir schema/cbioportal \
#       --prefix cbioportal \
#       [--model claude-haiku-4-5-20251001] \
#       [--no-alias]        # skip the LLM step (no API key / offline)
#
# The alias step reads ANTHROPIC_API_KEY from the environment. Without it (or
# with --no-alias) the first three artifacts are still produced.
# =============================================================================

suppressWarnings(suppressMessages({
  library(OmicsMLRepoCuration)   # tableToLinkmlSchema()
  library(jsonlite)
}))

# ---- tiny arg parser --------------------------------------------------------
parse_args <- function(args) {
  opts <- list(
    input  = "schema/cbioportal/cbioportal_schema.csv",
    outdir = NULL,                 # defaults to dirname(input)
    prefix = NULL,                 # defaults to basename(input) minus _schema.csv
    model  = "claude-haiku-4-5-20251001",
    schema_domain = "cancer_genomics",
    no_alias = FALSE
  )
  i <- 1
  while (i <= length(args)) {
    a <- args[[i]]
    take <- function() { i <<- i + 1; args[[i]] }
    switch(a,
      "--input"         = { opts$input  <- take() },
      "--outdir"        = { opts$outdir <- take() },
      "--prefix"        = { opts$prefix <- take() },
      "--model"         = { opts$model  <- take() },
      "--schema-domain" = { opts$schema_domain <- take() },
      "--no-alias"      = { opts$no_alias <- TRUE },
      stop("Unknown argument: ", a)
    )
    i <- i + 1
  }
  if (is.null(opts$outdir)) opts$outdir <- dirname(opts$input)
  if (is.null(opts$prefix)) {
    # Strip an optional "_schema" together with ".csv" in one pass, so
    # "cbioportal_schema.csv" -> "cbioportal" (matches the Python default).
    opts$prefix <- sub("(_schema)?\\.csv$", "", basename(opts$input))
  }
  opts
}

# Corpus types whose `allowedvalues` is an explicit value set (vs a regex).
ENUM_CORPUS_TYPES    <- c("static_enum", "custom_enum", "binary")
NUMERIC_COL_CLASSES  <- c("integer", "double", "numeric")

# TRUE where any "|"-token of corpus.type is an enum type. Handles compound
# types like "dynamic_enum|static_enum" so their explicit allowedvalues (the
# static half) are still emitted as an enum value set. Vectorized over `ct`.
is_enum_corpus_type <- function(ct) {
  vapply(ct, function(x) {
    if (is.na(x)) return(FALSE)
    any(trimws(strsplit(x, "\\|")[[1]]) %in% ENUM_CORPUS_TYPES)
  }, logical(1), USE.NAMES = FALSE)
}

# ---- 0. read the schema table ----------------------------------------------
# Schema tables are comma-separated CSV (write.csv()). Files still in the
# legacy write.table() layout (space-separated, quoted) are detected from the
# header line and read with read.table(). check.names=FALSE keeps the dotted
# column names (col.name, col.class, ...) intact.
read_schema <- function(path) {
  header <- readLines(path, n = 1L, warn = FALSE)
  if (grepl('^\\s*"?[^",]*"?\\s*,', header)) {
    read.csv(path, stringsAsFactors = FALSE, na.strings = "NA",
             check.names = FALSE)
  } else {
    read.table(path, header = TRUE, stringsAsFactors = FALSE,
               na.strings = "NA", check.names = FALSE)
  }
}

# ---- 1. LinkML schema -------------------------------------------------------
write_linkml <- function(dict, outdir, prefix) {
  out <- file.path(outdir, paste0(prefix, ".linkml.yaml"))
  class_name <- paste0(
    toupper(substring(prefix, 1, 1)), substring(prefix, 2), "Record"
  )
  tableToLinkmlSchema(
    dict,
    schema_id          = paste0("https://w3id.org/omicsmlrepo/", prefix),
    schema_name        = prefix,
    schema_description = paste0(prefix, " schema for the MetaHarmonizer ecosystem"),
    class_name         = class_name,
    output_file        = out
  )
  message("[1/4] LinkML schema         -> ", out)
  out
}

# ---- 2. target attrs (field_name, is_numeric_field) ------------------------
write_target_attrs <- function(dict, outdir, prefix) {
  out <- file.path(outdir, paste0(prefix, "_target_attrs.csv"))
  ta <- data.frame(
    field_name       = dict$col.name,
    is_numeric_field = ifelse(dict$col.class %in% NUMERIC_COL_CLASSES,
                              "yes", "no"),
    stringsAsFactors = FALSE
  )
  write.csv(ta, out, row.names = FALSE)
  message("[2/4] Target attrs          -> ", out,
          " (", nrow(ta), " fields)")
  list(path = out, df = ta)
}

# ---- 3. allowed-values JSON (SchemaMapper value dict) ----------------------
write_allowed_values <- function(dict, outdir, prefix) {
  out <- file.path(outdir, paste0(prefix, "_target_attrs_allowed_values.json"))
  is_enum <- is_enum_corpus_type(dict$corpus.type) &
             !is.na(dict$allowedvalues) & dict$allowedvalues != ""
  enum <- dict[is_enum, , drop = FALSE]
  values <- lapply(enum$allowedvalues, function(v) {
    trimws(strsplit(v, "\\|")[[1]])
  })
  names(values) <- enum$col.name
  # auto_unbox = FALSE keeps single-value fields as JSON arrays (the value dict
  # is always list-valued); named list preserves schema order.
  write_json(values, out, pretty = TRUE, auto_unbox = FALSE)
  message("[3/4] Allowed values JSON   -> ", out,
          " (", length(values), " enum fields)")
  out
}

# ---- 4. LLM alias dictionary (needs API key) -------------------------------
write_alias_dict <- function(target_df, outdir, prefix, model, schema_domain) {
  out      <- file.path(outdir, paste0(prefix, "_target_attrs_alias_haiku.csv"))
  meta_out <- file.path(outdir, paste0(prefix, "_target_attrs_alias_haiku.meta.json"))

  if (!requireNamespace("MetaHarmonizerR", quietly = TRUE)) {
    message("[4/4] Alias dict            -> SKIPPED (MetaHarmonizerR not installed).")
    return(invisible(NULL))
  }
  api_key <- Sys.getenv("ANTHROPIC_API_KEY", unset = "")
  if (!nzchar(api_key)) {
    message("[4/4] Alias dict            -> SKIPPED (ANTHROPIC_API_KEY not set).")
    return(invisible(NULL))
  }

  started <- format(Sys.time(), "%Y-%m-%dT%H:%M:%S")
  aliases <- MetaHarmonizerR::generate_alias_dict(
    target_fields = target_df,          # carries field_name + is_numeric_field
    model         = model,
    api_key       = api_key,
    schema_domain = schema_domain
  )
  finished <- format(Sys.time(), "%Y-%m-%dT%H:%M:%S")

  write.csv(aliases, out, row.names = FALSE)

  meta <- list(
    script          = "build_schema_artifacts.R",
    model           = model,
    prompt_version  = schema_domain,
    source_schema   = paste0(prefix, "_target_attrs.csv"),
    n_target_fields = nrow(target_df),
    n_alias_rows    = nrow(aliases),
    n_unique_sources = length(unique(aliases$source)),
    started_at      = started,
    finished_at     = finished
  )
  write_json(meta, meta_out, pretty = TRUE, auto_unbox = TRUE)
  message("[4/4] Alias dict            -> ", out,
          " (", nrow(aliases), " rows)")
  message("      Alias provenance      -> ", meta_out)
  invisible(out)
}

# ---- main -------------------------------------------------------------------
main <- function() {
  opts <- parse_args(commandArgs(trailingOnly = TRUE))
  if (!dir.exists(opts$outdir)) dir.create(opts$outdir, recursive = TRUE)

  message("Reading schema: ", opts$input)
  dict <- read_schema(opts$input)

  write_linkml(dict, opts$outdir, opts$prefix)
  ta <- write_target_attrs(dict, opts$outdir, opts$prefix)
  write_allowed_values(dict, opts$outdir, opts$prefix)
  if (!opts$no_alias) {
    write_alias_dict(ta$df, opts$outdir, opts$prefix,
                     opts$model, opts$schema_domain)
  } else {
    message("[4/4] Alias dict            -> SKIPPED (--no-alias).")
  }
  message("Done.")
}

if (identical(environment(), globalenv())) main()
