from datetime import datetime, timezone
from pathlib import Path

import pytest

from ic_feed.filtering import load_rules, matches_rules
from ic_feed.models import PaperRecord


def record(title, abstract=""):
    return PaperRecord(title=title, abstract=abstract, authors=[], journal="", published_at=datetime(2026, 10, 1, tzinfo=timezone.utc), doi="10.1000/ic", url="https://doi.org/10.1000/ic", sources=["test"], source_ids=["test:ic"])


@pytest.mark.parametrize("title,abstract", [
    ("SystemVerilog constrained-random verification for a DMA engine", ""),
    ("UVM coverage-driven test generation", "A hardware SoC verification study"),
    ("Formal verification of cache coherence", "A model checking framework for processors"),
    ("RISC-V pipeline design with low-power clock gating", ""),
    ("Logic synthesis and timing optimization", "RTL netlists"),
    ("High-level synthesis for FPGA accelerators", ""),
    ("Scan compression for digital design for test", "ASIC circuit"),
    ("A 3nm neural-network accelerator", "A digital chip with a reconfigurable dataflow"),
    ("An energy-efficient digital compute-in-memory macro", "SRAM circuits for multiplication"),
    ("A 6T SRAM macro with low-voltage operation", ""),
])
def test_accepts_digital_ic_candidates(title, abstract):
    assert matches_rules(record(title, abstract), load_rules(Path("config/queries.json")))


@pytest.mark.parametrize("title,abstract", [
    ("Formal verification of web application security", "Software model checking"),
    ("Integrated care treatment outcomes", "IC medical review"),
    ("RTL language translation", "Natural language processing"),
    ("Assertion generation for Python programs", "Software testing"),
    ("Synthesis of novel organic molecules", "Chemistry"),
    ("UVM spectrophotometry", "Medical measurement"),
    ("Using microarchitectures in an architectural facade", "Building materials"),
    ("An accelerator for industrial business growth", "Financial portfolio analysis"),
])
def test_rejects_ambiguous_or_unrelated_terms(title, abstract):
    assert not matches_rules(record(title, abstract), load_rules(Path("config/queries.json")))
