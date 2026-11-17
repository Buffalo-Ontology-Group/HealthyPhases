from phases_v2.sql import any_row, batches, in_list, literal


def test_literal_quotes_a_plain_value():
    assert literal("abc") == "'abc'"


def test_literal_escapes_embedded_quotes():
    assert literal("O'Brien") == "'O''Brien'"


def test_literal_neutralises_an_injection_attempt():
    hostile = "x' OR '1'='1"

    assert literal(hostile) == "'x'' OR ''1''=''1'"


def test_in_list_builds_a_parenthesised_list():
    assert in_list(["a", "b"]) == "('a', 'b')"


def test_an_empty_in_list_matches_nothing_rather_than_being_a_syntax_error():
    assert in_list([]) == "(NULL)"


def test_batches_cut_a_list_into_runs():
    assert [list(b) for b in batches([1, 2, 3, 4, 5], 2)] == [[1, 2], [3, 4], [5]]
    assert list(batches([], 2)) == []


def test_any_row_matches_each_row_on_every_column():
    rows = [{"k": "a", "s": 3, "x": "ignored"}, {"k": "O'Brien", "s": 4}]

    assert (
        any_row(["k", "s"], rows) == "(k = 'a' AND s = 3) OR (k = 'O''Brien' AND s = 4)"
    )


def test_any_row_of_no_rows_matches_nothing():
    assert any_row(["k"], []) == "FALSE"


def test_any_row_matches_a_missing_value_with_is_null():
    assert any_row(["a", "b"], [{"a": "x", "b": None}]) == "(a = 'x' AND b IS NULL)"
