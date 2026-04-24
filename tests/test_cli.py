"""End-to-end CLI test: run against a synthetic folder and verify outputs."""

from __future__ import annotations

from pathlib import Path

from click.testing import CliRunner
from PIL import Image

from imgproc.cli import main


def test_cli_processes_folder_and_writes_outputs(folder_of_mixed: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(
        main,
        [str(folder_of_mixed), "--no-open-report"],
        catch_exceptions=False,
    )
    assert result.exit_code == 0, result.output

    processed = folder_of_mixed / "processed"
    assert processed.exists()
    assert len(list(processed.glob("*.jpg"))) == 4  # three small + one outlier

    # Every output must be exactly the target canvas size.
    for out in processed.glob("*.jpg"):
        assert Image.open(out).size == (600, 800)

    # Report and assets.
    assert (folder_of_mixed / "report.html").exists()
    assert (folder_of_mixed / "_report_assets").is_dir()


def test_cli_dry_run_writes_no_files(folder_of_mixed: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(
        main,
        [str(folder_of_mixed), "--dry-run", "--no-open-report"],
        catch_exceptions=False,
    )
    assert result.exit_code == 0
    assert not (folder_of_mixed / "processed").exists()
    assert not (folder_of_mixed / "report.html").exists()


def test_cli_target_ratio_override(folder_of_mixed: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(
        main,
        [str(folder_of_mixed), "--target-ratio", "0.25", "--no-open-report"],
        catch_exceptions=False,
    )
    assert result.exit_code == 0, result.output
    # With a forced larger target, the small circles should be upscaled where allowed.
    # max_upscale defaults to 1.0, so the cap will kick in — but outputs must still be 600x800.
    for out in (folder_of_mixed / "processed").glob("*.jpg"):
        assert Image.open(out).size == (600, 800)
