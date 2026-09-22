"""``SeekAPI`` hands SEEK credentials to ``curl`` as one shell word, whatever characters the password holds.

``runGetQuery``, ``runSilentQuery`` and ``apiPost`` build a ``curl`` command line as a string and run it with
``shell=True``, and the credentials went in as ``-u '<username>:<password>'``. A password holding a single quote
therefore ended the word early and the rest of it was read by the shell rather than passed to curl. Every logged-in
page that asks SEEK for the caller's person record runs this with the password from the session
(``SeekDB.getUserInfo``). Now the pair is quoted with ``shlex.quote``.

Hermetic: ``subprocess.Popen`` is replaced; nothing runs.
"""

import shlex
from unittest.mock import MagicMock, patch

import pytest

from seek.seekapi import SeekAPI

HOSTILE = "pa'ss; touch /tmp/owned; echo '"


@pytest.fixture
def commands():
    ran = []

    def _popen(args, **_kwargs):
        ran.append(args[0])
        process = MagicMock()
        process.communicate.return_value = (b'{"data": {}}', b"")
        return process

    with patch("seek.seekapi.subprocess.Popen", side_effect=_popen):
        yield ran


@pytest.mark.parametrize("password", [HOSTILE, "plain-password", "with space", 'dq"uote', "back\\slash$HOME"])
def test_the_credentials_are_one_word_of_the_command_line(commands, password):
    SeekAPI("http://seek:3000", "member", password).runGetQuery("/people/5")

    (command,) = commands
    words = shlex.split(command)
    assert words[:3] == ["curl", "-u", "member:" + password]
    assert words[3:] == ["-k", "-X", "GET", "http://seek:3000/people/5", "-H", "accept: application/json"]


def test_the_silent_query_quotes_them_too(commands):
    SeekAPI("http://seek:3000", "member", HOSTILE).runSilentQuery("/projects.xml")

    assert shlex.split(commands[0])[:3] == ["curl", "-u", "member:" + HOSTILE]


def test_no_credentials_still_means_no_user_option(commands):
    SeekAPI("http://seek:3000", None, None).runGetQuery("/people/5")

    assert "-u" not in shlex.split(commands[0])
