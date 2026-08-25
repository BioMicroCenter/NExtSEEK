"""Behavioural tests for the attribute-filter tables in
:mod:`seek.dbtable_sampleattribute`.

Written for Step 20, which replaced five if/elif ladders with lookup tables.
The expected values below were captured from the ladders *before* that change
and are literals on purpose: a test that re-derives its expectation from the
table it is testing proves nothing.

No database is needed. ``getOperators`` reads only ``self.getAttributeInfo``,
which ``_FakeAttributes`` supplies, and the four ``filter*`` methods never touch
``self`` at all, so they are called unbound with ``None``.

Several of the expectations look wrong and are deliberate -- they record what
the code has always done:

* ``filterNumeric`` treats ``Less`` and ``Greater`` as inclusive, so a value
  equal to the bound passes both.
* ``toFloat`` maps ``None``, ``''`` and unparseable text to ``0.0``, so those
  values compare as zero rather than being rejected.
* ``filterDate``'s ``Not Equal`` passes a value that will not parse as a date,
  while every other date rule rejects it.
* Attribute types 4, 16, 17 and 18 fall to the default string operator set,
  which lists ``No Filter`` first; types 5-14 and 19-21 use a different string
  set that lists ``Contain`` first. Both report ``filter_type == 'string'``.
"""

import pytest

from seek.dbtable_sampleattribute import DBtable_sampleattribute

# attribute-type id -> (operator names in order, placeholder_start,
#                       placeholder_end, filter_type)
GOLDEN_OPERATORS = {
    1:  (['No Filter', 'Equal', 'Not Equal', 'Before', 'After', 'Between'],
         'mm/dd/year', 'mm/dd/year', 'date'),
    2:  (['No Filter', 'Equal', 'Not Equal', 'Before', 'After', 'Between'],
         'mm/dd/year', 'mm/dd/year', 'date'),
    3:  (['No Filter', 'Equal', 'Not Equal', 'Less', 'Greater', 'Between'],
         'numeric value', 'numeric value', 'numeric'),
    4:  (['No Filter', 'Contain', 'Not Contain'], 'string value', 'not in use', 'string'),
    5:  (['Contain', 'Not Contain', 'No Filter'], 'string value', 'not in use', 'string'),
    6:  (['Contain', 'Not Contain', 'No Filter'], 'string value', 'not in use', 'string'),
    7:  (['Contain', 'Not Contain', 'No Filter'], 'string value', 'not in use', 'string'),
    8:  (['Contain', 'Not Contain', 'No Filter'], 'string value', 'not in use', 'string'),
    9:  (['Contain', 'Not Contain', 'No Filter'], 'string value', 'not in use', 'string'),
    10: (['Contain', 'Not Contain', 'No Filter'], 'string value', 'not in use', 'string'),
    11: (['Contain', 'Not Contain', 'No Filter'], 'string value', 'not in use', 'string'),
    12: (['Contain', 'Not Contain', 'No Filter'], 'string value', 'not in use', 'string'),
    13: (['Contain', 'Not Contain', 'No Filter'], 'string value', 'not in use', 'string'),
    14: (['Contain', 'Not Contain', 'No Filter'], 'string value', 'not in use', 'string'),
    15: (['No Filter', 'True', 'False'], 'not in use', 'not in use', 'bool'),
    16: (['No Filter', 'Contain', 'Not Contain'], 'string value', 'not in use', 'string'),
    17: (['No Filter', 'Contain', 'Not Contain'], 'string value', 'not in use', 'string'),
    18: (['No Filter', 'Contain', 'Not Contain'], 'string value', 'not in use', 'string'),
    19: (['Contain', 'Not Contain', 'No Filter'], 'string value', 'not in use', 'string'),
    20: (['Contain', 'Not Contain', 'No Filter'], 'string value', 'not in use', 'string'),
    21: (['Contain', 'Not Contain', 'No Filter'], 'string value', 'not in use', 'string'),
    # out of range, both directions -- the fallback
    0:  (['No Filter', 'Contain', 'Not Contain'], 'string value', 'not in use', 'string'),
    99: (['No Filter', 'Contain', 'Not Contain'], 'string value', 'not in use', 'string'),
}

VALUES = [None, '', 'abc', '0', '1', '2.5', 3, '01/02/2024', 'yes']
DATE_VALUES = [None, '', 'abc', '01/02/2024', '12/31/1999', '06/15/2024']

GOLDEN_FILTERSTRING = {           # filter_valueFrom='a'
    'Contain':     [False, False, True, False, False, False, False, False, False],
    'Not Contain': [True, True, False, True, True, True, True, True, True],
    'No Filter':   [True, True, True, True, True, True, True, True, True],
}
GOLDEN_FILTERBOOL = {             # filter_valueFrom=''
    'True':      [False, False, False, False, True, False, False, False, True],
    'False':     [True, True, True, True, False, True, True, True, False],
    'No Filter': [True, True, True, True, True, True, True, True, True],
}
GOLDEN_FILTERNUMERIC = {          # from='1' to='3'
    'Equal':     [False, False, False, False, True, False, False, False, False],
    'Not Equal': [True, True, True, True, False, True, True, True, True],
    'Less':      [True, True, True, True, True, False, False, True, True],
    'Greater':   [False, False, False, False, True, True, True, False, False],
    'Between':   [False, False, False, False, True, True, True, False, False],
    'No Filter': [True, True, True, True, True, True, True, True, True],
}
GOLDEN_FILTERDATE = {             # from='01/02/2024' to='12/31/2024'
    'Equal':     [False, False, False, True, False, False],
    'Not Equal': [True, True, True, False, True, True],
    'Before':    [False, False, False, True, True, False],
    'After':     [False, False, False, True, False, True],
    'Between':   [False, False, False, True, False, True],
    'No Filter': [True, True, True, True, True, True],
}


class _FakeAttributes:
    """The only part of ``self`` that ``getOperators`` reads."""

    def __init__(self, attribute_type_id):
        self._id = attribute_type_id

    def getAttributeInfo(self, sampletype_id):
        return {'headers': ['attr'], 'attributeTypes': {'attr': self._id}}


@pytest.mark.parametrize('type_id', sorted(GOLDEN_OPERATORS))
def test_getOperators_matches_the_pre_step_20_ladder(type_id):
    names, start, end, filter_type = GOLDEN_OPERATORS[type_id]
    data = DBtable_sampleattribute.getOperators(_FakeAttributes(type_id), 1, 'attr')

    assert [o['name'] for o in data['filter_rule']] == names
    assert [o['operator'] for o in data['filter_rule']] == names
    assert data['placeholder_start'] == start
    assert data['placeholder_end'] == end
    assert data['filter_type'] == filter_type
    assert data['status'] == 1
    # exactly one option is preselected, and it is the first
    assert [o.get('selected') for o in data['filter_rule']] == [True] + [None] * (len(names) - 1)


def test_getOperators_accepts_a_string_attribute_type_id():
    """The id arrives from the database and is int()ed before the lookup."""
    data = DBtable_sampleattribute.getOperators(_FakeAttributes('15'), 1, 'attr')
    assert data['filter_type'] == 'bool'


def test_getOperators_returns_fresh_option_dicts_each_call():
    """The tables are module-level; the dicts handed out must not be shared."""
    a = DBtable_sampleattribute.getOperators(_FakeAttributes(3), 1, 'attr')
    b = DBtable_sampleattribute.getOperators(_FakeAttributes(3), 1, 'attr')
    a['filter_rule'][0]['name'] = 'MUTATED'
    assert b['filter_rule'][0]['name'] == 'No Filter'


def test_getOperators_reports_a_sample_type_with_no_attributes():
    class _NoHeaders:
        def getAttributeInfo(self, sampletype_id):
            return {'headers': [], 'attributeTypes': {}}

    data = DBtable_sampleattribute.getOperators(_NoHeaders(), 1, 'attr')
    assert data['status'] == 0
    assert data['filter_rule'] == []
    assert data['filter_type'] == ''
    assert data['msg'] == "Error: the sample type has no attribute defined. "


def test_getOperators_reports_an_unknown_attribute():
    data = DBtable_sampleattribute.getOperators(_FakeAttributes(3), 1, 'missing')
    assert data['status'] == 0
    assert data['filter_rule'] == []
    assert data['msg'] == "Error: the sample attribute not available. "


@pytest.mark.parametrize('rule', sorted(GOLDEN_FILTERSTRING))
def test_filterString(rule):
    got = DBtable_sampleattribute.filterString(None, VALUES, rule, 'a', '')
    assert got == GOLDEN_FILTERSTRING[rule]


@pytest.mark.parametrize('rule', sorted(GOLDEN_FILTERBOOL))
def test_filterBool(rule):
    got = DBtable_sampleattribute.filterBool(None, VALUES, rule, '', '')
    assert got == GOLDEN_FILTERBOOL[rule]


@pytest.mark.parametrize('rule', sorted(GOLDEN_FILTERNUMERIC))
def test_filterNumeric(rule):
    got = DBtable_sampleattribute.filterNumeric(None, VALUES, rule, '1', '3')
    assert got == GOLDEN_FILTERNUMERIC[rule]


@pytest.mark.parametrize('rule', sorted(GOLDEN_FILTERDATE))
def test_filterDate(rule):
    got = DBtable_sampleattribute.filterDate(None, DATE_VALUES, rule, '01/02/2024', '12/31/2024')
    assert got == GOLDEN_FILTERDATE[rule]


@pytest.mark.parametrize('method,args', [
    ('filterString', ('a', '')),
    ('filterBool', ('', '')),
    ('filterNumeric', ('1', '3')),
    ('filterDate', ('01/02/2024', '12/31/2024')),
])
def test_an_unknown_rule_passes_every_value(method, args):
    """Each ladder's else branch appended True for every value."""
    fn = getattr(DBtable_sampleattribute, method)
    values = DATE_VALUES if method == 'filterDate' else VALUES
    assert fn(None, values, 'Bogus Rule', *args) == [True] * len(values)
    assert fn(None, [], 'Bogus Rule', *args) == []
