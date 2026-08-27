"""The importable half of `output-skill-bayesian`.

The skill directory next door is named with a hyphen, which is not a Python
identifier, so nothing under it can be imported -- and a script that cannot be
imported cannot be unit tested. `output-skill/scripts/` is the standing proof:
both of its scripts had rotted in the same direction and nothing noticed,
because nothing imported them (see `test_output_skill_scripts.py`'s docstring).

So the logic lives here and `output-skill-bayesian/scripts/merge_grades.py` is a
three-line entry point over it. The skill still has a path to name in `SKILL.md`;
the code still has tests.
"""
