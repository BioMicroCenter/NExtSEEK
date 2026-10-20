# SEEK user scripts

The three fixed Ruby scripts cc-spawner runs inside the `seek` container for the superuser user API
(`nextseek_api/services/users.py`): `create.rb`, `patch.rb` and `compensate.rb`. Each reads its payload with
`JSON.parse(ENV.fetch("NEXTSEEK_PAYLOAD"))`, so the payload travels in the exec's environment, never in its
command line, and Ruby never interpolates it. Any other script name is refused (404). See `../README.md`.
