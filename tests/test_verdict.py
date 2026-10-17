from app.checks import build_verdict, check_fit, run_checks
from app.domain.provenance import Provenance, UNKNOWN, fact
from app.domain.result import CheckStatus, Outcome
from tests.factories import device, facility, intel


def test_a_device_that_fits_everywhere_returns_works():
    verdict = check_fit(device(), facility(), intel())

    assert verdict.outcome is Outcome.WORKS
    assert not verdict.requirements


def test_one_blocking_failure_outranks_every_passing_check():
    # Arrange: a 60 Hz device on a 50 Hz grid, everything else fine
    dev = device(frequencies_hz=fact([60], Provenance.NAMEPLATE))
    site = facility(mains_frequency_hz=50)

    # Act
    verdict = check_fit(dev, site, intel())

    # Assert
    assert verdict.outcome is Outcome.WONT_WORK
    assert len(verdict.failures) == 1
    assert len(verdict.passes) > 5


def test_mitigable_gaps_become_the_list_of_things_to_also_send():
    dev = device(
        voltage_min_v=fact(110, Provenance.NAMEPLATE),
        voltage_max_v=fact(120, Provenance.NAMEPLATE),
        consumables=fact(["test strips"], Provenance.SPEC_SHEET),
    )
    site = facility(mains_voltage_v=240)
    research = intel(consumables_available=fact(False, Provenance.MODEL_KNOWLEDGE))

    verdict = check_fit(dev, site, research)

    assert verdict.outcome is Outcome.WORKS_IF
    assert len(verdict.requirements) == 2
    assert any("transformer" in r for r in verdict.requirements)


def test_missing_facts_become_follow_up_questions_not_guesses():
    dev = device(frequencies_hz=UNKNOWN, consumables=UNKNOWN)

    verdict = check_fit(dev, facility(), intel())

    assert len(verdict.questions) == 2
    assert all("?" in q for q in verdict.questions)


def test_results_are_ordered_worst_first():
    dev = device(frequencies_hz=fact([60], Provenance.NAMEPLATE))
    site = facility(mains_frequency_hz=50)

    results = run_checks(dev, site, intel())

    assert results[0].status is CheckStatus.FAIL


def test_the_verdict_is_the_same_every_time_for_the_same_facts():
    # The whole point of judging in code: no model, no variance.
    args = (device(), facility(), intel())

    first = build_verdict(run_checks(*args))
    second = build_verdict(run_checks(*args))

    assert first == second
