from dataclasses import replace

import pytest

from ic_feed.venues import Venue, load_venues, match_venue
from test_domain import record


@pytest.mark.parametrize("venue,id", [
    ("IEEE Transactions on Computer-Aided Design of Integrated Circuits and Systems", "tcad"),
    ("IEEE Transactions on Very Large Scale Integration (VLSI) Systems", "tvlsi"),
    ("ACM Transactions on Design Automation of Electronic Systems", "todaes"),
    ("IEEE Transactions on Computers", "tc"),
    ("IEEE Journal of Solid-State Circuits", "jssc"),
    ("Proceedings of the 63rd ACM/IEEE Design Automation Conference", "dac"),
    ("2026 IEEE/ACM International Conference On Computer Aided Design (ICCAD)", "iccad"),
    ("2026 Formal Methods in Computer-Aided Design (FMCAD)", "fmcad"),
    ("Computer Aided Verification", "cav"),
    ("Proceedings of the 53rd Annual International Symposium on Computer Architecture", "isca"),
    ("MICRO-59: 2026 59th Annual IEEE/ACM International Symposium on Microarchitecture", "micro"),
    ("2026 IEEE International Symposium on High-Performance Computer Architecture (HPCA)", "hpca"),
    ("Proceedings of the 31st ACM International Conference on Architectural Support for Programming Languages and Operating Systems, Volume 2", "asplos"),
    ("2025 IEEE International Solid-State Circuits Conference (ISSCC)", "isscc"),
    ("2025 Symposium on VLSI Technology and Circuits (VLSI Technology and Circuits)", "vlsi"),
    ("2024 IEEE Symposium on VLSI Technology and Circuits (VLSI Technology and Circuits)", "vlsi"),
    ("2021 Symposium on VLSI Circuits", "vlsi"),
])
def test_admits_verified_metadata_variants(venue, id):
    assert match_venue(replace(record("Verilog generation"), journal=venue)).id == id


@pytest.mark.parametrize("venue", [
    "", "arXiv", "IEEE Access", "Electronics", "Workshop on Design Automation Conference",
    "Proceedings of the ASPLOS Companion", "International Symposium on Computer Architecture Workshops",
    "Design Automation Conference Companion", "Computer Aided Verification Tutorial",
    "Future Transactions on Computers", "Journal claiming IEEE Transactions on Computers",
    "International Solid-State Circuits Conference Workshops",
    "International Conference on VLSI Design",
    "Regional Symposium on VLSI Technology and Circuits",
    "IEEE International Symposium on Circuits and Systems",
])
def test_rejects_unknown_venue_preprints_and_nonmain_tracks(venue):
    assert match_venue(replace(record("Verilog at DAC TCAD"), journal=venue)) is None


def test_only_explicit_flagship_whitelist_is_loaded():
    assert {v.id for v in load_venues()} == {
        "tcad", "tvlsi", "todaes", "tc", "jssc", "dac", "iccad", "fmcad",
        "cav", "isca", "micro", "hpca", "asplos", "isscc", "vlsi",
    }


def test_rejects_mechanical_asme_dac_even_when_title_matches():
    item = replace(record("Digital simulation"), doi="10.1115/detc1988-0021", journal="Design Automation Conference")
    assert match_venue(item) is None


def test_cav_is_recognized_in_multi_container_metadata_but_not_generic_lncs():
    item = replace(record("Formal hardware verification"), journal="Lecture Notes in Computer Science · Computer Aided Verification")
    assert match_venue(item).id == "cav"
    assert match_venue(replace(item, journal="Lecture Notes in Computer Science")) is None


@pytest.mark.parametrize("venue", [
    "2026 IEEE Asia and South Pacific Design Automation Conference (ASP-DAC)",
    "Asia South Pacific Design Automation Conference",
    "2026 International Conference on Computer-Aided Design and Computer Graphics (CAD/Graphics)",
    "Journal claiming Design Automation Conference",
    "Computer Aided Verification and Analysis",
    "Proceedings of the Student Research Competition at the Design Automation Conference",
])
def test_rejects_other_conferences_with_flagship_name_substrings(venue):
    item = replace(record("RTL hardware design"), journal=venue, doi="10.1109/ASPDAC.2026.123")
    assert match_venue(item) is None


def test_conference_matching_supports_explicit_additional_aliases():
    configured = (Venue("custom", "Explicit Future Conference", "conference", ("explicit future conference",)),)
    item = replace(record("RTL hardware design"), journal="2026 10th IEEE Explicit Future Conference (CUSTOM), Volume 1")
    assert match_venue(item, configured).id == "custom"
    assert match_venue(replace(item, journal="Regional Explicit Future Conference"), configured) is None


def test_vlsi_uses_configured_venue_name_without_restricting_valid_ieee_doi_prefix():
    item = replace(
        record("Digital accelerator design"),
        journal="2025 Symposium on VLSI Technology and Circuits (VLSI Technology and Circuits)",
        doi="10.23919/vlsitechnologyandcir65189.2025.11075209",
    )
    assert match_venue(item).id == "vlsi"
