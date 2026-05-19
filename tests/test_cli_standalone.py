"""Verify CLI argument parsing for standalone commands."""
import argparse
import pytest


def test_run_hl_args_parsed():
    """run-hl subcommand should accept --input and --quiet."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", "-p", default=".")
    parser.add_argument("--config", "-c")
    sub = parser.add_subparsers(dest="command")
    p = sub.add_parser("run-hl")
    p.add_argument("--input", required=True)
    p.add_argument("--quiet", "-q", action="store_true")

    args = parser.parse_args(["run-hl", "--input", "roadmap.md"])
    assert args.input == "roadmap.md"
    assert args.quiet is False

    args2 = parser.parse_args(["run-hl", "--input", "-", "-q"])
    assert args2.input == "-"
    assert args2.quiet is True


def test_run_fr_args_parsed():
    """run-fr subcommand should accept --spec and --task-id."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", "-p", default=".")
    sub = parser.add_subparsers(dest="command")
    p = sub.add_parser("run-fr")
    p.add_argument("--spec", required=True)
    p.add_argument("--task-id")
    p.add_argument("--quiet", "-q", action="store_true")

    args = parser.parse_args(["run-fr", "--spec", "auth.md", "--task-id", "auth"])
    assert args.spec == "auth.md"
    assert args.task_id == "auth"


def test_run_fi_branch_and_pr_mutually_exclusive():
    """run-fi should not accept both --branch and --pr."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", "-p", default=".")
    sub = parser.add_subparsers(dest="command")
    p = sub.add_parser("run-fi")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--branch")
    g.add_argument("--pr", type=int)
    p.add_argument("--task-id")
    p.add_argument("--dev-cmd")
    p.add_argument("--dev-ready")
    p.add_argument("--base-url")
    p.add_argument("--quiet", "-q", action="store_true")

    args = parser.parse_args(["run-fi", "--branch", "feat/x"])
    assert args.branch == "feat/x"
    assert args.pr is None

    args2 = parser.parse_args(["run-fi", "--pr", "42"])
    assert args2.pr == 42
    assert args2.branch is None

    with pytest.raises(SystemExit):
        parser.parse_args(["run-fi", "--branch", "x", "--pr", "42"])
