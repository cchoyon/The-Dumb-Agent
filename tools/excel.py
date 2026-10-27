import xl_engine


def excel_rules_help() -> str:
    """Show the format of the rules JSON used by excel_validate_rules and excel_build. Call it once before writing rules."""
    return xl_engine.RULES_HELP


def excel_inspect(path: str) -> str:
    """Step 1 of turning a messy text/CSV file into a clean Excel workbook. Safely inspects the file (read-only), saves an immutable snapshot, records its SHA-256, detects encoding / format / delimiter, shows sample lines and a draft rules JSON. Changes nothing.

    Args:
        path: Path to the source file (data.txt, .csv, .tsv, .log, ...). Relative paths are inside the project folder; absolute paths are allowed.
    """
    return xl_engine.inspect_file(path)


def excel_validate_rules(path: str, rules: dict, max_units: int = 2000) -> str:
    """Step 2: dry-run your rules on the file (nothing is written). Reports how many records parse cleanly, which go to the review queue and why (with raw examples), and the first parsed rows. Repeat with improved rules until the review reasons are understood. Required before excel_build.

    Args:
        path: Path to the source file.
        rules: The rules object (see excel_rules_help). A JSON string is also accepted.
        max_units: How many records to check (default 2000). Use 0 to check the whole file.
    """
    return xl_engine.validate(path, rules, max_units)


def excel_build(path: str, rules: dict, output_path: str = "", max_review_percent: float = 5.0) -> str:
    """Step 3: parse the whole file with the validated rules, clean explicitly, write the .xlsx, reopen it and verify what was actually saved (structure, every cell value and type, row fingerprints) against the reconciliation ledger. Writes an audit folder next to the workbook. Only report success if the result says VERIFIED.

    Args:
        path: Path to the source file.
        rules: The same rules that were validated with excel_validate_rules.
        output_path: Output .xlsx path inside the project folder. Default: <source name>_clean.xlsx.
        max_review_percent: Stop before writing if more than this percent of records need review. Only raise it after the user agrees.
    """
    return xl_engine.build(path, rules, output_path, max_review_percent)


def excel_verify(output_path: str) -> str:
    """Re-verify an existing workbook made by excel_build against its audit folder and the original source hash (detects later edits or corruption).

    Args:
        output_path: Path to the .xlsx made by excel_build.
    """
    return xl_engine.verify_existing(output_path)
