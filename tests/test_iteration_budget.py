"""Agent-loop iteration budget (run_agent.IterationBudget).

Caps tool-calling turns per agent so a runaway loop can't spin forever;
execute_code turns are refunded so they don't eat the budget.
"""

from run_agent import IterationBudget


def test_consume_until_exhausted():
    b = IterationBudget(3)
    assert b.consume() is True
    assert b.consume() is True
    assert b.consume() is True
    assert b.consume() is False  # budget exhausted
    assert b.used == 3
    assert b.remaining == 0


def test_refund_gives_a_turn_back():
    b = IterationBudget(2)
    b.consume()
    b.consume()
    assert b.consume() is False
    b.refund()
    assert b.remaining == 1
    assert b.consume() is True


def test_refund_never_goes_negative():
    b = IterationBudget(1)
    b.refund()  # nothing consumed yet
    assert b.used == 0
    assert b.remaining == 1


def test_zero_budget_blocks_immediately():
    b = IterationBudget(0)
    assert b.consume() is False
