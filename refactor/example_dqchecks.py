import argparse
import shutil
import tempfile
import time
from pathlib import Path

import pandas as pd
import openpyxl
import dqchecks


def copy_to_tmp(source: Path, target_dir: Path) -> Path:
    """Copy a file into target_dir and return the destination path."""
    target_dir.mkdir(parents=True, exist_ok=True)
    destination = target_dir / f"{int(time.time()*1000)}_{source.name}"
    shutil.copy2(source, destination)
    return destination


def perform_checks(template_path: Path, company_path: Path) -> pd.DataFrame:
    temp_dir = Path(tempfile.gettempdir()) / "dqchecks-uploads"
    template_copy = copy_to_tmp(template_path, temp_dir)
    company_copy = copy_to_tmp(company_path, temp_dir)

    wb_template = openpyxl.open(template_copy, data_only=False)
    wb_template_dataonly = openpyxl.open(template_copy, data_only=True)

    wb_company = openpyxl.open(company_copy, data_only=False)
    wb_company_dataonly = openpyxl.open(company_copy, data_only=True)

    outputs = []
    result = dqchecks.panacea.find_formula_differences(wb_template, wb_company)
    if isinstance(result, pd.DataFrame):
        outputs.append(result)
    result = dqchecks.panacea.find_formula_errors(wb_company_dataonly)
    if isinstance(result, pd.DataFrame):
        outputs.append(result)
    result = dqchecks.panacea.find_missing_sheets(wb_template_dataonly, wb_company_dataonly)
    if isinstance(result, pd.DataFrame):
        outputs.append(result)
    result = dqchecks.panacea.find_shape_differences(wb_template, wb_company)
    if isinstance(result, pd.DataFrame):
        outputs.append(result)
    result = dqchecks.panacea.find_pk_errors(wb_company_dataonly, "^fOut_", "Reference")
    if isinstance(result, pd.DataFrame):
        outputs.append(result)
    result = dqchecks.panacea.find_pk_errors(wb_company_dataonly, "^fOut_", "Reference")
    if isinstance(result, pd.DataFrame):
        outputs.append(result)

    if not outputs:
        return pd.DataFrame()

    union_df = pd.concat(outputs, ignore_index=True).drop_duplicates().reset_index(drop=True)
    return union_df


def main():
    parser = argparse.ArgumentParser(description="Run dqchecks on a template and company workbook.")
    parser.add_argument("template", type=Path, help="Path to the template XLSX file.")
    parser.add_argument("company", type=Path, help="Path to the company XLSX file.")
    args = parser.parse_args()

    perform_checks(args.template, args.company)


if __name__ == "__main__":
    main()
