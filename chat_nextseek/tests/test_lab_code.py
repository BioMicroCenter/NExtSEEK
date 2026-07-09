from chat_nextseek.helpers.lab_code import lab_code


def test_first_three_letters_uppercased():
    assert lab_code("Kamm") == "KAM"
    assert lab_code("Shalek") == "SHA"
    assert lab_code("Engelward") == "ENG"


def test_strips_trailing_lab_word_and_non_alpha():
    assert lab_code("Shalek lab") == "SHA"
    assert lab_code("  kamm ") == "KAM"
    assert lab_code("O'Brien") == "OBR"


def test_too_short_returns_empty():
    assert lab_code("Li") == ""
    assert lab_code("") == ""
    assert lab_code(None) == ""
