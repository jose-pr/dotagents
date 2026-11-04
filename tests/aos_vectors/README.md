# AOS conformance vectors

JSON data an implementation in any language tests against. The format of each file, the comparison
rules and the classes they serve are in
[Conformance](https://boriken-dev.github.io/agent-overlay-spec/spec/v0/conformance/#vectors).

| File | What it checks |
| --- | --- |
| `reading.json` | Format line, TOML default, JSON and YAML data model, static reading, limits, `dotenv` and its substitution, env-script output |
| `validation.json` | Every field, valid and invalid, and files a field names |
| `names.json` | Overlay identities, normalization, root variables |
| `source.json` | `[git+]<location>[@<ref>][#<path>]` |
| `digest.json` | The tree digest |
| `compose.json` | `requires`, cycles, composition order, contributions, environment across overlays (values chained from one overlay to the next), and the defaults of `rules`, `context`, `skills` and `paths` |
| `effective.json` | One overlay's environment, setup, setup phases, runtime, needs and weakenings on a given OS, with the defaults of `paths`, `env_files`, `env_scripts` and `setup` |

Error classes:

- reading: `invalid-utf8`, `no-format-named`, `unsupported-format`, `parse-error`, `not-a-mapping`,
  `null-value`, `duplicate-key`, `non-string-key`, `datetime-value`, `non-finite-float`,
  `integer-range`, `invalid-unicode`, `yaml-tag`, `yaml-anchor`, `yaml-merge-key`,
  `yaml-directive`, `too-deep`, `too-large`, `dotenv-syntax`, `env-script-output`;
- substituting, in `dotenv` reading cases and when applying: `undefined-variable`,
  `secret-placeholder`, `invalid-pattern`;
- digest: `symlink`, `special-file`, `non-utf8-name`, `invalid-name`, `case-collision`;
- applying: `unresolved-reference`, `invalid-reference`, `missing-file`, `invalid-env-file`,
  `env-path-conflict`, `reserved-variable`;
- resolving: `cycle`, `invalid-source`.

`python -m pytest tests/test_vectors.py tests/test_digest.py` runs them through this repository's
reference reader.
